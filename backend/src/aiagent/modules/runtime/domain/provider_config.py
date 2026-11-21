"""Định nghĩa danh mục nhà cung cấp LLM (Gemini, Ollama, OpenAI, Claude, Groq, OpenRouter, DeepSeek)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from aiagent.modules.runtime.domain.model import ChatModelPort

ProviderKind = Literal["cloud", "local"]


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    name: str
    vendor: str
    description: str
    kind: ProviderKind
    requires_api_key: bool
    requires_base_url: bool
    default_base_url: str
    docs_url: str
    sample_models: tuple[str, ...]
    supports_free_only: bool = False


PROVIDER_CATALOG: tuple[ProviderDescriptor, ...] = (
    ProviderDescriptor(
        id="gemini",
        name="Google Gemini",
        vendor="Google AI",
        description=(
            "Mô hình đa phương thức Gemini (Flash, Flash-Lite, Pro…) qua API tương thích OpenAI. "
            "Hỗ trợ gói miễn phí từ Google AI Studio."
        ),
        kind="cloud",
        requires_api_key=True,
        requires_base_url=False,
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        docs_url="https://aistudio.google.com/apikey",
        # Bí danh "-latest" luôn trỏ tới model hiện hành — model 2.x đã ngừng cho tài khoản mới.
        sample_models=("gemini-flash-lite-latest", "gemini-flash-latest", "gemini-pro-latest"),
    ),
    ProviderDescriptor(
        id="ollama",
        name="Ollama",
        vendor="Local / Self-Hosted",
        description=(
            "Chạy mô hình mã nguồn mở ngay trên máy chủ nội bộ (Qwen, Llama, DeepSeek, Gemma…). "
            "Dữ liệu không rời hạ tầng — hỗ trợ dữ liệu mật (restricted)."
        ),
        kind="local",
        requires_api_key=False,
        requires_base_url=True,
        # Trong Docker: cầu nối tới Ollama trên máy host (service ollama-host); Ollama dạng
        # container (profile ollama) thì dùng http://ollama:11434.
        default_base_url="http://ollama-host:11434",
        docs_url="https://ollama.com/library",
        sample_models=("qwen3:4b", "qwen2.5:3b", "qwen2.5:7b", "qwen3:8b"),
    ),
    ProviderDescriptor(
        id="openai",
        name="OpenAI",
        vendor="OpenAI",
        description="Các mô hình GPT-4o, GPT-4.1, o3-mini và các endpoint tương thích chuẩn OpenAI.",
        kind="cloud",
        requires_api_key=True,
        requires_base_url=False,
        default_base_url="https://api.openai.com/v1",
        docs_url="https://platform.openai.com/api-keys",
        sample_models=("gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3-mini"),
    ),
    ProviderDescriptor(
        id="anthropic",
        name="Anthropic Claude",
        vendor="Anthropic",
        description="Dòng mô hình Claude (Opus, Sonnet, Haiku) mạnh về lập luận, lập trình và tool-calling.",
        kind="cloud",
        requires_api_key=True,
        requires_base_url=False,
        default_base_url="https://api.anthropic.com",
        docs_url="https://console.anthropic.com/settings/keys",
        sample_models=("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"),
    ),
    ProviderDescriptor(
        id="groq",
        name="Groq",
        vendor="Groq Cloud",
        description=(
            "Suy luận tốc độ siêu cao trên phần cứng LPU cho Llama, Qwen, Mixtral "
            "với hạn mức miễn phí."
        ),
        kind="cloud",
        requires_api_key=True,
        requires_base_url=False,
        default_base_url="https://api.groq.com/openai/v1",
        docs_url="https://console.groq.com/keys",
        sample_models=("llama-3.3-70b-versatile", "qwen-qwq-32b", "mixtral-8x7b-32768"),
    ),
    ProviderDescriptor(
        id="openrouter",
        name="OpenRouter",
        vendor="OpenRouter",
        description=(
            "Cổng kết nối hợp nhất hàng trăm mô hình LLM, hỗ trợ chế độ chỉ lọc "
            "các model miễn phí (:free)."
        ),
        kind="cloud",
        requires_api_key=True,
        requires_base_url=False,
        default_base_url="https://openrouter.ai/api/v1",
        docs_url="https://openrouter.ai/keys",
        sample_models=(
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "google/gemini-2.0-flash-exp:free",
        ),
        supports_free_only=True,
    ),
    ProviderDescriptor(
        id="deepseek",
        name="DeepSeek",
        vendor="DeepSeek",
        description="Mô hình DeepSeek-V3 và DeepSeek-R1 với khả năng suy luận mạnh mẽ và chi phí tối ưu.",
        kind="cloud",
        requires_api_key=True,
        requires_base_url=False,
        default_base_url="https://api.deepseek.com/v1",
        docs_url="https://platform.deepseek.com/api_keys",
        sample_models=("deepseek-chat", "deepseek-reasoner"),
    ),
)

PROVIDER_BY_ID: dict[str, ProviderDescriptor] = {p.id: p for p in PROVIDER_CATALOG}


@dataclass(frozen=True)
class ProviderRecord:
    tenant_id: str
    provider_id: str
    enabled: bool
    base_url: str | None
    models: list[str]
    extra: dict[str, Any]
    has_api_key: bool
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    latency_ms: int
    models: list[str] = field(default_factory=list)
    error: str | None = None


class ProviderRepositoryPort(Protocol):
    async def list_for_tenant(self, tenant_id: str) -> list[ProviderRecord]: ...
    async def get(self, tenant_id: str, provider_id: str) -> ProviderRecord | None: ...
    async def upsert(
        self,
        tenant_id: str,
        provider_id: str,
        *,
        enabled: bool,
        base_url: str | None,
        models: list[str],
        extra: dict[str, Any],
        has_api_key: bool,
        updated_by: str,
    ) -> ProviderRecord: ...
    async def delete(self, tenant_id: str, provider_id: str) -> bool: ...


class ProviderFactoryPort(Protocol):
    def build(
        self,
        provider_id: str,
        *,
        base_url: str | None,
        api_key: str | None,
        extra: dict[str, Any],
    ) -> ChatModelPort: ...

    async def probe(
        self,
        provider_id: str,
        *,
        base_url: str | None,
        api_key: str | None,
        extra: dict[str, Any],
    ) -> ProbeResult: ...
