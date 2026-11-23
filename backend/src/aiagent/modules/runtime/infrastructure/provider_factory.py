"""Factory tạo ChatModelPort và kiểm tra kết nối sống (probe) cho các nhà cung cấp LLM."""

from __future__ import annotations

import time
from typing import Any

import httpx

from aiagent.modules.runtime.domain import (
    PROVIDER_BY_ID,
    ChatModelPort,
    ProbeResult,
)
from aiagent.modules.runtime.infrastructure.anthropic_model import AnthropicChatModel
from aiagent.modules.runtime.infrastructure.openai_compat_model import (
    OpenAICompatChatModel,
    OpenAICompatConfig,
)


def _normalize_url(provider_id: str, base_url: str | None) -> str:
    desc = PROVIDER_BY_ID[provider_id]
    raw = (base_url or "").strip().rstrip("/") or desc.default_base_url.rstrip("/")
    if provider_id == "ollama" and not raw.endswith("/v1"):
        return f"{raw}/v1"
    return raw


class DefaultProviderFactory:
    def __init__(
        self,
        *,
        app_name: str = "ARIES",
        timeout_s: float = 600.0,
        fallback_models: dict[str, list[str]] | None = None,
        overload_retries: int = 2,
        retry_delay_s: float = 1.0,
    ) -> None:
        self._app_name = app_name
        self._timeout_s = timeout_s
        self._fallback = fallback_models or {}
        self._retries = overload_retries
        self._retry_delay_s = retry_delay_s

    def build(
        self,
        provider_id: str,
        *,
        base_url: str | None,
        api_key: str | None,
        extra: dict[str, Any],
    ) -> ChatModelPort:
        if provider_id == "anthropic":
            url = (base_url or "").strip() or None
            return AnthropicChatModel(api_key or "", base_url=url)

        desc = PROVIDER_BY_ID[provider_id]
        url = _normalize_url(provider_id, base_url)
        free_only = bool(extra.get("free_only", True)) if provider_id == "openrouter" else False
        extra_headers = {"X-Title": self._app_name} if provider_id == "openrouter" else {}
        cfg = OpenAICompatConfig(
            provider=provider_id,
            base_url=url,
            api_key=api_key or None,
            label=desc.name,
            send_reasoning_effort=provider_id == "gemini",
            model_suffix=":free" if free_only else None,
            extra_headers=extra_headers,
            fallback_models=tuple(self._fallback.get(provider_id, [])),
            overload_retries=self._retries,
            retry_delay_s=self._retry_delay_s,
        )
        return OpenAICompatChatModel(cfg, timeout_s=self._timeout_s)

    async def probe(
        self,
        provider_id: str,
        *,
        base_url: str | None,
        api_key: str | None,
        extra: dict[str, Any],
    ) -> ProbeResult:
        desc = PROVIDER_BY_ID[provider_id]
        if desc.requires_api_key and not (api_key or "").strip():
            return ProbeResult(ok=False, latency_ms=0, error="Chưa nhập API key.")

        t0 = time.monotonic()
        try:
            if provider_id == "anthropic":
                models = await self._probe_anthropic(base_url, api_key or "")
            else:
                models = await self._probe_openai_compat(provider_id, base_url, api_key, extra)
        except httpx.TimeoutException:
            ms = int((time.monotonic() - t0) * 1000)
            return ProbeResult(
                ok=False,
                latency_ms=ms,
                error=f"Kết nối tới {desc.name} quá thời gian chờ (timeout).",
            )
        except httpx.HTTPStatusError as exc:
            ms = int((time.monotonic() - t0) * 1000)
            code = exc.response.status_code
            if code in (401, 403):
                msg = f"API key của {desc.name} không hợp lệ hoặc không có quyền truy cập (HTTP {code})."
            elif code == 404:
                msg = f"Không tìm thấy endpoint của {desc.name} (HTTP 404). Hãy kiểm tra lại Base URL."
            else:
                msg = f"{desc.name} trả về lỗi HTTP {code}."
            return ProbeResult(ok=False, latency_ms=ms, error=msg)
        except httpx.HTTPError as exc:
            ms = int((time.monotonic() - t0) * 1000)
            hint = (
                " Hãy đảm bảo Ollama đang chạy và địa chỉ có thể truy cập từ backend."
                if provider_id == "ollama"
                else ""
            )
            return ProbeResult(
                ok=False,
                latency_ms=ms,
                error=f"Không thể kết nối tới {desc.name}: {type(exc).__name__}.{hint}",
            )
        except ValueError:
            ms = int((time.monotonic() - t0) * 1000)
            return ProbeResult(
                ok=False,
                latency_ms=ms,
                error=f"Phản hồi từ {desc.name} không đúng định dạng JSON.",
            )

        ms = int((time.monotonic() - t0) * 1000)
        return ProbeResult(ok=True, latency_ms=ms, models=models)

    async def _probe_anthropic(self, base_url: str | None, api_key: str) -> list[str]:
        root = (base_url or "").strip().rstrip("/") or "https://api.anthropic.com"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=6.0)) as client:
            resp = await client.get(f"{root}/v1/models", headers=headers)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        return sorted(
            {str(item.get("id", "")) for item in data if isinstance(item, dict) and item.get("id")}
        )

    async def _probe_openai_compat(
        self,
        provider_id: str,
        base_url: str | None,
        api_key: str | None,
        extra: dict[str, Any],
    ) -> list[str]:
        url = _normalize_url(provider_id, base_url)
        headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if provider_id == "openrouter":
            headers["X-Title"] = self._app_name

        # Ollama nội bộ không đi qua proxy ngoài nếu trust_env=False; cloud giữ trust_env=True.
        trust_env = provider_id != "ollama"
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=6.0),
            headers=headers,
            trust_env=trust_env,
        ) as client:
            resp = await client.get(f"{url}/models")
            resp.raise_for_status()
            data = resp.json().get("data", [])

        suffix = ":free" if (provider_id == "openrouter" and extra.get("free_only", True)) else None
        ids: list[str] = []
        for item in data:
            mid = str(item.get("id", "")) if isinstance(item, dict) else ""
            mid = mid.removeprefix("models/")
            if not mid or (suffix and not mid.endswith(suffix)):
                continue
            ids.append(mid)
        return sorted(set(ids))
