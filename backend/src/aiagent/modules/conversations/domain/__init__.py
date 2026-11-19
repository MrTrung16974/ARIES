from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from aiagent.shared_kernel.usage import Usage


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUSED = "refused"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Thread:
    id: UUID
    tenant_id: str
    user_id: str
    agent_id: UUID
    agent_version: int
    is_draft: bool
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Message:
    id: int
    thread_id: UUID
    run_id: UUID | None
    role: str  # "user" | "assistant" (theo Messages API)
    content: list[dict[str, Any]]
    created_at: datetime


@dataclass(frozen=True)
class Run:
    id: UUID
    thread_id: UUID
    status: RunStatus
    model: str
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    error: str | None = None


class ConversationRepositoryPort(Protocol):
    async def create_thread(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: UUID,
        agent_version: int,
        is_draft: bool,
        title: str,
    ) -> Thread: ...

    async def get_thread(self, tenant_id: str, thread_id: UUID) -> Thread | None: ...

    async def list_threads(
        self, tenant_id: str, user_id: str, agent_id: UUID | None, limit: int
    ) -> list[Thread]: ...

    async def list_messages(self, tenant_id: str, thread_id: UUID) -> list[Message]: ...

    async def append_message(
        self,
        tenant_id: str,
        thread_id: UUID,
        run_id: UUID | None,
        role: str,
        content: list[dict[str, Any]],
    ) -> Message: ...

    async def start_run(
        self,
        tenant_id: str,
        thread_id: UUID,
        agent_id: UUID,
        agent_version: int,
        user_id: str,
        model: str,
    ) -> Run: ...

    async def finish_run(
        self,
        tenant_id: str,
        run_id: UUID,
        status: RunStatus,
        usage: Usage,
        cost_usd: float,
        error: str | None,
    ) -> None: ...


__all__ = [
    "ConversationRepositoryPort",
    "Message",
    "Run",
    "RunStatus",
    "Thread",
    "Usage",
]
