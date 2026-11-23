"""Adapter cho mọi API tương thích OpenAI Chat Completions: Gemini, Groq, OpenRouter, Ollama…

Lịch sử trong ARIES theo định dạng Messages API (content block). Adapter dịch qua lại:

- `text` → content; `tool_use` → `tool_calls`; `tool_result` → tin `role: tool`.
- `thinking`, `redacted_thinking`, `fallback`… không echo (provider khác không hiểu).
- Lượt trả về được dựng lại thành block `text` / `tool_use` để vòng lặp, lưu trữ và
  ToolGateway dùng chung một định dạng với Claude.
- `reasoning_content` / `reasoning` (Groq, OpenRouter, Ollama, DeepSeek…) → `ThinkingDelta`,
  không lưu vào lịch sử; `<think>…</think>` trong text (model local kiểu qwen3/deepseek-r1)
  được tách ra.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from aiagent.modules.runtime.domain import (
    ChatMessage,
    ModelEvent,
    ModelRateLimitedError,
    ModelRequest,
    ModelTurn,
    ModelUnavailableError,
    StructuredRequest,
    StructuredResult,
    TextDelta,
    ThinkingDelta,
    ToolCallStarted,
    ToolDefinition,
    TurnCompleted,
)
from aiagent.shared_kernel.usage import Usage

logger = logging.getLogger(__name__)

_MAX_JSON_RETRIES = 2
_MODELS_TTL_S = 300.0
_FINISH = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}
# effort của ARIES → reasoning_effort (provider chỉ nhận low/medium/high).
_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


@dataclass(frozen=True)
class OpenAICompatConfig:
    provider: str  # tên provider trong ARIES: gemini | groq | openrouter | ollama
    base_url: str  # tới /v1 (không có dấu / cuối), ví dụ https://api.groq.com/openai/v1
    api_key: str | None = None
    label: str = ""  # tên hiển thị trong thông báo lỗi
    # Gửi `reasoning_effort` (Gemini nhận; Groq/Ollama chỉ một số model nhận → tắt).
    send_reasoning_effort: bool = False
    # Lọc danh sách model tự lấy từ /models (ví dụ OpenRouter chỉ lấy ":free").
    model_suffix: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    # Khi model quá tải (5xx) hoặc bị giới hạn (429): thử lại cùng model `overload_retries` lần
    # (chờ retry_delay_s, 2×, 4×…), rồi lần lượt chuyển sang các model dự phòng.
    fallback_models: tuple[str, ...] = ()
    overload_retries: int = 0  # bootstrap đặt theo settings.llm_overload_retries
    retry_delay_s: float = 1.0


class ModelOverloadedError(ModelUnavailableError):
    """Provider quá tải/lỗi tạm thời (5xx) — thử lại hoặc chuyển model dự phòng được."""


# ---------------------------------------------------------------------- chuyển đổi định dạng
def provider_message(body: str, limit: int = 300) -> str:
    """Lấy `error.message` từ body lỗi (OpenAI: object; Gemini: list[object]) — gọn, một dòng."""
    try:
        data: Any = json.loads(body)
    except ValueError:
        return ""
    if isinstance(data, list) and data:
        data = data[0]
    err = data.get("error") if isinstance(data, dict) else None
    msg = err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else None
    if not msg:
        return ""
    text = " ".join(str(msg).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(str(c.get("text", "")))
            elif isinstance(c, dict):
                parts.append(f"[{c.get('type', 'block')}]")
        text = "\n".join(parts)
    else:
        text = str(content)
    return f"LỖI: {text}" if block.get("is_error") else text


def to_openai_messages(system: str, messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})
    for m in messages:
        raw = m.get("content", [])
        blocks: list[dict[str, Any]] = (
            [{"type": "text", "text": raw}] if isinstance(raw, str) else list(raw)
        )
        if m["role"] == "assistant":
            text = "".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
            calls = [
                {
                    "id": str(b.get("id")),
                    "type": "function",
                    "function": {
                        "name": str(b.get("name")),
                        "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                    },
                }
                for b in blocks
                if b.get("type") == "tool_use"
            ]
            if not text and not calls:
                continue
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        # user: tool_result phải đứng ngay sau tin assistant có tool_calls, rồi mới tới text.
        for b in blocks:
            if b.get("type") == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(b.get("tool_use_id")),
                        "content": _tool_result_text(b),
                    }
                )
        text = "\n".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
        if text:
            out.append({"role": "user", "content": text})
    return out


def to_openai_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema or {"type": "object", "properties": {}},
            },
        }
        for t in tools
    ]


def _strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip() if "<think>" in text else text


class _ThinkSplitter:
    """Tách `<think>…</think>` khỏi luồng text (thẻ có thể bị cắt giữa các chunk)."""

    _OPEN, _CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, chunk: str) -> list[ModelEvent]:
        self._buf += chunk
        events: list[ModelEvent] = []
        while self._buf:
            tag = self._CLOSE if self._inside else self._OPEN
            idx = self._buf.find(tag)
            if idx >= 0:
                self._emit(self._buf[:idx], events)
                self._buf = self._buf[idx + len(tag) :]
                self._inside = not self._inside
                continue
            # Giữ lại phần đuôi có thể là đầu của thẻ.
            keep = next(
                (
                    k
                    for k in range(min(len(tag) - 1, len(self._buf)), 0, -1)
                    if tag.startswith(self._buf[-k:])
                ),
                0,
            )
            self._emit(self._buf[: len(self._buf) - keep], events)
            self._buf = self._buf[len(self._buf) - keep :]
            break
        return events

    def flush(self) -> list[ModelEvent]:
        events: list[ModelEvent] = []
        self._emit(self._buf, events)
        self._buf = ""
        return events

    def _emit(self, text: str, events: list[ModelEvent]) -> None:
        if text:
            events.append(ThinkingDelta(text) if self._inside else TextDelta(text))


# ---------------------------------------------------------------------- adapter
class OpenAICompatChatModel:
    def __init__(
        self,
        config: OpenAICompatConfig,
        *,
        timeout_s: float = 600.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cfg = config
        headers = {"Content-Type": "application/json", **config.extra_headers}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        # trust_env=True: đi qua HTTPS_PROXY (Squid) cho cloud; Ollama nằm trong NO_PROXY.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0), headers=headers
        )
        self._models_cache: tuple[float, list[str]] | None = None

    @property
    def provider(self) -> str:
        return self._cfg.provider

    @property
    def _label(self) -> str:
        return self._cfg.label or self._cfg.provider

    def _url(self, path: str) -> str:
        return f"{self._cfg.base_url.rstrip('/')}/{path.lstrip('/')}"

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ danh sách model
    async def list_models(self) -> list[str]:
        """Model provider đang có (Ollama: model đã pull). Cache 5 phút; lỗi → danh sách rỗng."""
        now = time.monotonic()
        if self._models_cache and now - self._models_cache[0] < _MODELS_TTL_S:
            return self._models_cache[1]
        try:
            resp = await self._client.get(self._url("models"), timeout=10.0)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Không lấy được danh sách model của %s: %s", self._label, exc)
            return self._models_cache[1] if self._models_cache else []
        ids = []
        for item in data:
            mid = str(item.get("id", "")) if isinstance(item, dict) else ""
            mid = mid.removeprefix("models/")  # Gemini trả "models/gemini-…"
            if not mid or (self._cfg.model_suffix and not mid.endswith(self._cfg.model_suffix)):
                continue
            ids.append(mid)
        ids = sorted(set(ids))
        self._models_cache = (now, ids)
        return ids

    # ------------------------------------------------------------------ chat
    def _payload(self, req: ModelRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": to_openai_messages(req.system, req.messages),
            "max_tokens": req.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if req.tools:
            payload["tools"] = to_openai_tools(req.tools)
        if self._cfg.send_reasoning_effort:
            payload["reasoning_effort"] = _EFFORT.get(req.effort, "medium")
        return payload

    def _raise_for_status(self, status: int, body: str, model: str) -> None:
        if status < 400:
            return
        if status == 429:
            # Ghi lại chi tiết: 429 có thể là rate limit tạm thời hoặc hết/không có quota (limit: 0).
            logger.warning("%s 429 (model %s): %s", self._label, model, body[:1000])
            raise ModelRateLimitedError()
        if status in (401, 403):
            raise ModelUnavailableError(f"API key của {self._label} không hợp lệ hoặc thiếu quyền.")
        detail = provider_message(body)
        suffix = f" — {self._label}: {detail}" if detail else ""
        if status == 404:
            hint = f" (chạy: ollama pull {model})" if self._cfg.provider == "ollama" else ""
            # Ví dụ Gemini: model vẫn có trong /models nhưng "no longer available to new users".
            raise ModelUnavailableError(
                f"{self._label} không dùng được model '{model}'{hint}{suffix}. "
                "Hãy chọn model khác trong cấu hình agent."
            )
        logger.error("%s lỗi %s: %s", self._label, status, body[:2000])
        if status == 400:
            raise ModelUnavailableError(f"Yêu cầu gửi tới model không hợp lệ{suffix}.")
        if status in (500, 502, 503, 504):
            raise ModelOverloadedError(f"{self._label} đang quá tải, thử lại sau{suffix}.")
        raise ModelUnavailableError()

    # ------------------------------------------------------------------ retry + fallback
    def _candidates(self, model: str) -> list[str]:
        return [model, *(m for m in dict.fromkeys(self._cfg.fallback_models) if m != model)]

    async def _backoff(self, model: str, attempt: int, exc: Exception) -> None:
        delay = self._cfg.retry_delay_s * (2**attempt)
        logger.warning(
            "%s model %s lỗi tạm thời (%s) — thử lại sau %.1fs", self._label, model, exc, delay
        )
        await asyncio.sleep(delay)

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Như `_stream_model`, kèm thử lại khi quá tải và chuyển model dự phòng.

        Chỉ thử lại khi chưa phát sự kiện nào (lỗi xảy ra trước khi model trả nội dung) —
        đã stream dở thì báo lỗi luôn để không lặp/trộn câu trả lời."""
        last: ModelUnavailableError | ModelRateLimitedError | None = None
        for i, model in enumerate(self._candidates(request.model)):
            if i:
                logger.warning(
                    "%s chuyển sang model dự phòng %s (model %s lỗi: %s)",
                    self._label,
                    model,
                    request.model,
                    last,
                )
            attempt = 0
            while True:
                emitted = False
                try:
                    async for event in self._stream_model(request, model):
                        emitted = True
                        yield event
                    return
                except ModelOverloadedError as exc:
                    if emitted:
                        raise
                    last = exc
                    if attempt >= self._cfg.overload_retries:
                        break
                    await self._backoff(model, attempt, exc)
                    attempt += 1
                except ModelRateLimitedError as exc:
                    # 429 thường là hết quota/giới hạn theo model → đổi model thay vì chờ.
                    if emitted:
                        raise
                    last = exc
                    break
        raise last or ModelUnavailableError()

    async def _stream_model(self, request: ModelRequest, model: str) -> AsyncIterator[ModelEvent]:
        payload = {**self._payload(request), "model": model}
        attempt = 0
        while True:
            try:
                async for event in self._stream_once(payload, model):
                    yield event
                return
            except ValueError:
                attempt += 1
                if attempt > _MAX_JSON_RETRIES:
                    raise ModelUnavailableError("Model trả về input tool không hợp lệ.") from None
                logger.warning("Input tool không parse được, thử lại lượt (%d)", attempt)
            except httpx.TimeoutException as exc:
                raise ModelUnavailableError(f"{self._label} phản hồi quá lâu.") from exc
            except httpx.HTTPError as exc:
                raise ModelUnavailableError(f"Không kết nối được tới {self._label}.") from exc

    async def _stream_once(self, payload: dict[str, Any], model: str) -> AsyncIterator[ModelEvent]:
        text_parts: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        finish: str | None = None
        usage = Usage()
        model_name = model
        splitter = _ThinkSplitter()

        async with self._client.stream("POST", self._url("chat/completions"), json=payload) as r:
            if r.status_code >= 400:
                body = (await r.aread()).decode(errors="replace")
                self._raise_for_status(r.status_code, body, model)
            async for line in r.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if err := chunk.get("error"):
                    # OpenRouter/Gemini có thể báo lỗi giữa stream.
                    code = str(err.get("code") if isinstance(err, dict) else "")
                    self._raise_for_status(
                        int(code) if code.isdigit() else 500,
                        json.dumps(err, ensure_ascii=False),
                        model,
                    )
                model_name = chunk.get("model") or model_name
                if u := chunk.get("usage"):
                    usage = Usage(
                        input_tokens=int(u.get("prompt_tokens") or 0),
                        output_tokens=int(u.get("completion_tokens") or 0),
                        cache_read_input_tokens=int(
                            (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                        ),
                    )
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if isinstance(reasoning, str) and reasoning:
                        yield ThinkingDelta(reasoning)
                    if piece := delta.get("content"):
                        text_parts.append(piece)
                        for ev in splitter.feed(piece):
                            yield ev
                    for tc in delta.get("tool_calls") or []:
                        idx = int(tc.get("index", len(calls)))
                        slot = calls.setdefault(idx, {"id": "", "name": "", "args": ""})
                        fn = tc.get("function") or {}
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
                        if slot["name"] and not slot.get("started"):
                            slot["id"] = slot["id"] or f"call_{uuid.uuid4().hex[:24]}"
                            slot["started"] = True
                            yield ToolCallStarted(id=slot["id"], name=slot["name"])
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
        for ev in splitter.flush():
            yield ev

        content: list[dict[str, Any]] = []
        text = _strip_think("".join(text_parts))
        if text:
            content.append({"type": "text", "text": text})
        for idx in sorted(calls):
            slot = calls[idx]
            if not slot["name"]:
                continue
            args = slot["args"].strip()
            parsed = json.loads(args) if args else {}  # ValueError → stream() sinh lại lượt
            if not isinstance(parsed, dict):
                raise ValueError("tool input không phải object")
            content.append(
                {
                    "type": "tool_use",
                    "id": slot["id"] or f"call_{uuid.uuid4().hex[:24]}",
                    "name": slot["name"],
                    "input": parsed,
                }
            )
        has_tools = any(b["type"] == "tool_use" for b in content)
        # Gemini đôi khi trả finish_reason "stop" kèm tool_calls.
        stop_reason = "tool_use" if has_tools else _FINISH.get(finish or "stop", "end_turn")
        yield TurnCompleted(
            ModelTurn(content=content, stop_reason=stop_reason, usage=usage, model=model_name)
        )

    # ------------------------------------------------------------------ structured outputs
    async def generate_json(self, request: StructuredRequest) -> StructuredResult:
        """`_generate_json_model` kèm thử lại khi quá tải và chuyển model dự phòng."""
        last: ModelUnavailableError | ModelRateLimitedError | None = None
        for i, model in enumerate(self._candidates(request.model)):
            if i:
                logger.warning(
                    "%s chuyển sang model dự phòng %s (model %s lỗi: %s)",
                    self._label,
                    model,
                    request.model,
                    last,
                )
            for attempt in range(self._cfg.overload_retries + 1):
                try:
                    return await self._generate_json_model(request, model)
                except ModelOverloadedError as exc:
                    last = exc
                    if attempt < self._cfg.overload_retries:
                        await self._backoff(model, attempt, exc)
                except ModelRateLimitedError as exc:
                    last = exc
                    break
        raise last or ModelUnavailableError()

    async def _generate_json_model(
        self, request: StructuredRequest, model: str
    ) -> StructuredResult:
        """JSON theo schema qua `response_format: json_schema`; provider không hỗ trợ →
        lùi về `json_object` + schema trong system prompt."""
        base: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
        }
        if self._cfg.send_reasoning_effort:
            base["reasoning_effort"] = _EFFORT.get(request.effort, "medium")
        attempts = [
            {
                **base,
                "messages": to_openai_messages(
                    request.system, [{"role": "user", "content": request.prompt}]
                ),
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "output", "schema": request.schema, "strict": True},
                },
            },
            {
                **base,
                "messages": to_openai_messages(
                    request.system
                    + "\n\nChỉ trả về MỘT object JSON hợp lệ theo JSON Schema sau, không kèm"
                    " giải thích:\n" + json.dumps(request.schema, ensure_ascii=False),
                    [{"role": "user", "content": request.prompt}],
                ),
                "response_format": {"type": "json_object"},
            },
        ]
        resp: httpx.Response | None = None
        for i, payload in enumerate(attempts):
            try:
                resp = await self._client.post(self._url("chat/completions"), json=payload)
            except httpx.TimeoutException as exc:
                raise ModelUnavailableError(f"{self._label} phản hồi quá lâu.") from exc
            except httpx.HTTPError as exc:
                raise ModelUnavailableError(f"Không kết nối được tới {self._label}.") from exc
            if resp.status_code == 400 and i == 0:
                logger.info("%s không nhận json_schema, lùi về json_object", self._label)
                continue
            self._raise_for_status(resp.status_code, resp.text, model)
            break
        if resp is None:  # pragma: no cover - attempts luôn có phần tử
            raise ModelUnavailableError()
        body = resp.json()
        choice = (body.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise ModelUnavailableError("Kết quả bị cắt (max_tokens).")
        text = _strip_think(str((choice.get("message") or {}).get("content") or ""))
        text = _FENCE_RE.sub("", text.strip())
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelUnavailableError("Model trả về JSON không hợp lệ.") from exc
        if not isinstance(data, dict):
            raise ModelUnavailableError("Model trả về JSON không phải object.")
        u = body.get("usage") or {}
        return StructuredResult(
            data=data,
            usage=Usage(
                input_tokens=int(u.get("prompt_tokens") or 0),
                output_tokens=int(u.get("completion_tokens") or 0),
            ),
        )
