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
    AWAITING_APPROVAL = "awaiting_approval"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


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


@dataclass(frozen=True)
class Approval:
    """Yêu cầu duyệt một tool_use (ADR-0016)."""

    id: UUID
    tenant_id: str
    thread_id: UUID
    run_id: UUID
    agent_id: UUID
    requester_id: str
    tool_use_id: str
    tool: str
    risk: str
    reason: str
    arguments: dict[str, Any]
    approver_roles: list[str]
    connection_id: str | None
    status: ApprovalStatus
    decided_by: str | None
    decision_note: str | None
    created_at: datetime
    decided_at: datetime | None


class DelegationStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"


@dataclass(frozen=True)
class Delegation:
    """Một lần Master giao việc cho agent khác (ADR-0017)."""

    id: UUID
    tenant_id: str
    parent_thread_id: UUID
    parent_run_id: UUID
    tool_use_id: str
    agent_id: UUID
    task: str
    child_thread_id: UUID | None
    child_run_id: UUID | None
    status: DelegationStatus
    result: str | None
    tainted: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Feedback:
    """Đánh giá một câu trả lời (ADR-0020)."""

    id: UUID
    tenant_id: str
    thread_id: UUID
    message_id: int
    agent_id: UUID
    user_id: str
    rating: int  # 1 | -1
    comment: str
    created_at: datetime


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

    async def create_approvals(
        self,
        tenant_id: str,
        thread_id: UUID,
        run_id: UUID,
        agent_id: UUID,
        requester_id: str,
        requests: list[dict[str, Any]],
    ) -> list[Approval]: ...

    async def get_approval(self, tenant_id: str, approval_id: UUID) -> Approval | None: ...

    async def list_approvals(
        self,
        tenant_id: str,
        *,
        status: ApprovalStatus | None,
        requester_id: str | None,
        thread_id: UUID | None,
        limit: int,
    ) -> list[Approval]: ...

    async def decide_approval(
        self,
        tenant_id: str,
        approval_id: UUID,
        status: ApprovalStatus,
        decided_by: str,
        note: str | None,
    ) -> Approval | None:
        """Chỉ đổi được khi đang pending (None nếu đã có người quyết định trước)."""
        ...

    async def approvals_for_tool_uses(
        self, tenant_id: str, thread_id: UUID, tool_use_ids: list[str]
    ) -> list[Approval]: ...

    async def expire_pending(self, tenant_id: str, thread_id: UUID) -> int: ...

    async def upsert_feedback(
        self,
        tenant_id: str,
        thread_id: UUID,
        message_id: int,
        agent_id: UUID,
        user_id: str,
        rating: int,
        comment: str,
    ) -> Feedback: ...

    async def feedback_for_thread(self, tenant_id: str, thread_id: UUID) -> list[Feedback]: ...

    async def create_delegation(
        self,
        tenant_id: str,
        parent_thread_id: UUID,
        parent_run_id: UUID,
        tool_use_id: str,
        agent_id: UUID,
        task: str,
    ) -> Delegation: ...

    async def update_delegation(
        self,
        tenant_id: str,
        delegation_id: UUID,
        *,
        status: DelegationStatus,
        child_thread_id: UUID | None,
        child_run_id: UUID | None,
        result: str | None,
        tainted: bool,
    ) -> None: ...

    async def delegations_for_tool_uses(
        self, tenant_id: str, parent_thread_id: UUID, tool_use_ids: list[str]
    ) -> list[Delegation]:
        """Mới nhất trước."""
        ...


__all__ = [
    "Approval",
    "ApprovalStatus",
    "ConversationRepositoryPort",
    "Delegation",
    "DelegationStatus",
    "Feedback",
    "Message",
    "Run",
    "RunStatus",
    "Thread",
    "Usage",
]
