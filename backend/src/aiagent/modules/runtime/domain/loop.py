"""Port vòng lặp agent. Hiện thực bằng LangGraph ở `runtime.infrastructure` (ADR-0014).

Vòng lặp phát `RunEvent`:
- `MESSAGE` {role, content}: tin nhắn hoàn chỉnh cần lưu (assistant hoặc user/tool_result).
- `APPROVAL_REQUIRED` {requests}: tool cần người duyệt — vòng lặp dừng (ADR-0016).
- `DONE` {status, error, usage, model}: luôn là sự kiện cuối.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from aiagent.modules.runtime.domain.events import RunEvent
from aiagent.modules.runtime.domain.model import (
    ChatMessage,
    ChatModelPort,
    ContentBlock,
    ModelRequest,
    ToolDefinition,
)


class Effect(StrEnum):
    ALLOW = "allow"
    APPROVE = "approve"  # cần người duyệt trước khi chạy
    DENY = "deny"


@dataclass(frozen=True)
class ToolDecision:
    effect: Effect
    tool_name: str  # tên trong manifest, ví dụ db.query
    risk: str
    reason: str = ""
    approver_roles: tuple[str, ...] = ()  # rỗng = người chạy run tự duyệt được
    connection_id: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


class ToolExecutorPort(Protocol):
    @property
    def definitions(self) -> list[ToolDefinition]: ...

    def decide(self, tool_use: ContentBlock) -> ToolDecision:
        """Policy cho một tool_use: chạy luôn, cần duyệt, hay từ chối."""
        ...

    async def execute(
        self, tool_use: ContentBlock, *, approved_by: str | None = None
    ) -> tuple[ContentBlock, dict[str, object]]:
        """Chạy một tool_use → (block tool_result gửi model, thông tin hiển thị cho UI)."""
        ...


@dataclass(frozen=True)
class LoopInput:
    model: ChatModelPort
    request: ModelRequest  # system, model, effort, max_tokens; messages = lịch sử + tin mới
    tools: ToolExecutorPort
    max_iterations: int
    max_tokens_per_run: int


class AgentLoopPort(Protocol):
    def run(self, loop: LoopInput) -> AsyncIterator[RunEvent]: ...


__all__ = [
    "AgentLoopPort",
    "ChatMessage",
    "Effect",
    "LoopInput",
    "ToolDecision",
    "ToolExecutorPort",
]
