"""Port gọi model — không phụ thuộc SDK cụ thể. Message/content theo định dạng Messages API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from aiagent.shared_kernel.errors import AppError
from aiagent.shared_kernel.usage import Usage

ContentBlock = dict[str, Any]
ChatMessage = dict[str, Any]  # {"role": "user"|"assistant", "content": list[ContentBlock]}


@dataclass(frozen=True)
class ToolDefinition:
    name: str  # tên an toàn cho API: ^[a-zA-Z0-9_-]{1,128}$
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelRequest:
    model: str
    system: str
    messages: list[ChatMessage]
    tools: list[ToolDefinition] = field(default_factory=list)
    effort: str = "medium"
    max_tokens: int = 16000


@dataclass(frozen=True)
class ModelTurn:
    content: list[ContentBlock]
    stop_reason: str | None
    usage: Usage
    model: str


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    text: str


@dataclass(frozen=True)
class ToolCallStarted:
    id: str
    name: str


@dataclass(frozen=True)
class TurnCompleted:
    turn: ModelTurn


ModelEvent = TextDelta | ThinkingDelta | ToolCallStarted | TurnCompleted


class ChatModelPort(Protocol):
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        """Stream một lượt của model; sự kiện cuối luôn là `TurnCompleted`."""
        ...


class ModelUnavailableError(AppError):
    code = "llm_unavailable"
    status_code = 503
    public_message = "Mô hình AI tạm thời không khả dụng."


class ModelRateLimitedError(AppError):
    code = "llm_rate_limited"
    status_code = 429
    public_message = "Mô hình AI đang quá tải, vui lòng thử lại sau."
