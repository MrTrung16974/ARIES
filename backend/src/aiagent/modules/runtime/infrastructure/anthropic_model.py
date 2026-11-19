"""Adapter Claude dùng SDK chính thức `anthropic` (ADR-0014).

- Thinking luôn bật trên model hiện tại; độ sâu điều khiển bằng `output_config.effort`.
- Không dùng temperature/top_p, không ép tool_choice.
- `display: "updates"` để nhận ghi chú tiến độ giữa các lần gọi tool.
- Server-side fallback khi bị từ chối do classifier (`fallbacks: "default"`).
- Thinking block được trả lại nguyên vẹn; `drop_block` để không lỗi 400 nếu prefix lệch.
- Prompt caching: breakpoint ở system + tự động cache khối cuối.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from aiagent.modules.runtime.domain import (
    ModelEvent,
    ModelRateLimitedError,
    ModelRequest,
    ModelTurn,
    ModelUnavailableError,
    TextDelta,
    ThinkingDelta,
    ToolCallStarted,
    TurnCompleted,
)
from aiagent.shared_kernel.usage import Usage

logger = logging.getLogger(__name__)

BETA_FALLBACK = "server-side-fallback-2026-07-01"
BETA_DISPLAY_UPDATES = "thinking-display-updates-2026-08-18"
BETA_THINKING_BINDING = "thinking-binding-controls-2026-08-01"

# Tính năng theo model (theo tài liệu Claude API, cache 2026-11-03).
_FEATURES: dict[str, frozenset[str]] = {
    "claude-opus-5-5": frozenset({"fallback", "updates", "binding"}),
    "claude-sonnet-5-5": frozenset({"fallback", "updates", "binding"}),
    "claude-haiku-5-5": frozenset(),
}

_MAX_JSON_RETRIES = 2


class AnthropicChatModel:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        max_retries: int = 2,
        timeout_s: float = 600.0,
        client: Any = None,
    ) -> None:
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, base_url=base_url, max_retries=max_retries, timeout=timeout_s
        )

    def _params(self, req: ModelRequest) -> dict[str, Any]:
        features = _FEATURES.get(req.model, frozenset())
        params: dict[str, Any] = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": [
                {"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": req.messages,
            "output_config": {"effort": req.effort},
            "cache_control": {"type": "ephemeral"},
        }
        betas: list[str] = []
        thinking: dict[str, Any] = {"type": "adaptive"}
        if "updates" in features:
            thinking["display"] = "updates"
            betas.append(BETA_DISPLAY_UPDATES)
        if "binding" in features:
            thinking["block_binding"] = {"prefix_mismatch_behavior": "drop_block"}
            betas.append(BETA_THINKING_BINDING)
        params["thinking"] = thinking
        if "fallback" in features:
            params["fallbacks"] = "default"
            betas.append(BETA_FALLBACK)
        if betas:
            params["betas"] = betas
        if req.tools:
            params["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    # Input stream ngay khi sinh; ToolGateway luôn validate lại theo schema.
                    "eager_input_streaming": True,
                }
                for t in req.tools
            ]
        return params

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        params = self._params(request)
        attempt = 0
        while True:
            try:
                async for event in self._stream_once(params):
                    yield event
                return
            except ValueError:
                # JSON input tool không parse được — chưa có tool_use_id để trả lỗi; sinh lại lượt.
                attempt += 1
                if attempt > _MAX_JSON_RETRIES:
                    raise ModelUnavailableError("Model trả về input tool không hợp lệ.") from None
                logger.warning("Input tool không parse được, thử lại lượt (%d)", attempt)
            except anthropic.RateLimitError as exc:
                raise ModelRateLimitedError() from exc
            except anthropic.AuthenticationError as exc:
                raise ModelUnavailableError("API key của Claude không hợp lệ.") from exc
            except anthropic.PermissionDeniedError as exc:
                raise ModelUnavailableError("API key không có quyền dùng model này.") from exc
            except anthropic.BadRequestError as exc:
                logger.error("Claude từ chối request: %s", exc.message)
                raise ModelUnavailableError("Yêu cầu gửi tới model không hợp lệ.") from exc
            except anthropic.APIStatusError as exc:
                logger.error("Claude lỗi %s: %s", exc.status_code, exc.message)
                raise ModelUnavailableError() from exc
            except anthropic.APIConnectionError as exc:
                raise ModelUnavailableError("Không kết nối được tới Claude API.") from exc

    async def _stream_once(self, params: dict[str, Any]) -> AsyncIterator[ModelEvent]:
        async with self._client.beta.messages.stream(**params) as stream:
            raw_event: Any
            async for raw_event in stream:
                event: Any = raw_event
                kind = getattr(event, "type", None)
                if kind == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", None) == "tool_use":
                        yield ToolCallStarted(id=block.id, name=block.name)
                elif kind == "content_block_delta":
                    delta = event.delta
                    delta_type = getattr(delta, "type", None)
                    if delta_type == "text_delta":
                        yield TextDelta(delta.text)
                    elif delta_type == "thinking_delta" and delta.thinking:
                        yield ThinkingDelta(delta.thinking)
            final = await stream.get_final_message()

        usage = final.usage
        yield TurnCompleted(
            ModelTurn(
                content=[
                    b.model_dump(mode="json", by_alias=True, exclude_none=True)
                    for b in final.content
                ],
                stop_reason=final.stop_reason,
                usage=Usage(
                    input_tokens=usage.input_tokens or 0,
                    output_tokens=usage.output_tokens or 0,
                    cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0)
                    or 0,
                ),
                model=final.model,
            )
        )
