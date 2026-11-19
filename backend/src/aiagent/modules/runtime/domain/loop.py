"""Port vòng lặp agent. Hiện thực bằng LangGraph ở `runtime.infrastructure` (ADR-0014).

Vòng lặp phát `RunEvent`:
- `MESSAGE` {role, content}: tin nhắn hoàn chỉnh cần lưu (assistant hoặc user/tool_result).
- `DONE` {status, error, usage, model}: luôn là sự kiện cuối.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from aiagent.modules.runtime.domain.events import RunEvent
from aiagent.modules.runtime.domain.model import (
    ChatMessage,
    ChatModelPort,
    ContentBlock,
    ModelRequest,
    ToolDefinition,
)


class ToolExecutorPort(Protocol):
    @property
    def definitions(self) -> list[ToolDefinition]: ...

    async def execute(self, tool_use: ContentBlock) -> tuple[ContentBlock, dict[str, object]]:
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


__all__ = ["AgentLoopPort", "ChatMessage", "LoopInput", "ToolExecutorPort"]
