from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.conversations.domain import Approval, ApprovalStatus
from aiagent.modules.identity.domain import Principal
from aiagent.modules.runtime.application import PreparedRun

router = APIRouter(prefix="/api/v1", tags=["runtime"])

AnyUser = Annotated[Principal, Depends(get_principal)]


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    thread_id: UUID | None = None
    draft: bool = False  # chạy bản mới nhất chưa publish (chỉ người sửa được agent)


class ThreadOut(BaseModel):
    id: UUID
    agent_id: UUID
    agent_version: int
    is_draft: bool
    title: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    id: int
    role: str
    content: list[dict[str, Any]]
    created_at: datetime


@router.post(
    "/agents/{agent_id}/chat",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def chat(agent_id: UUID, body: ChatIn, p: AnyUser, c: ContainerDep) -> StreamingResponse:
    """Chat với agent; trả về Server-Sent Events theo contract RunEvent (ADR-0005)."""
    prepared = await c.runs.prepare(
        p, agent_id, body.message, thread_id=body.thread_id, draft=body.draft
    )
    return StreamingResponse(
        _sse(c.runs.stream(prepared), prepared),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def _sse(events: Any, _: PreparedRun) -> AsyncIterator[str]:
    async for event in events:
        yield event.to_sse()


@router.get("/threads", response_model=list[ThreadOut])
async def list_threads(
    p: AnyUser,
    c: ContainerDep,
    agent_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ThreadOut]:
    threads = await c.conversations.threads(p, agent_id, limit)
    return [
        ThreadOut(
            id=t.id,
            agent_id=t.agent_id,
            agent_version=t.agent_version,
            is_draft=t.is_draft,
            title=t.title,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in threads
    ]


@router.get("/threads/{thread_id}/messages", response_model=list[MessageOut])
async def list_messages(thread_id: UUID, p: AnyUser, c: ContainerDep) -> list[MessageOut]:
    messages = await c.conversations.messages(p, thread_id)
    return [
        MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at)
        for m in messages
    ]


@router.post(
    "/threads/{thread_id}/resume",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def resume(thread_id: UUID, p: AnyUser, c: ContainerDep) -> StreamingResponse:
    """Tiếp tục run đã dừng chờ duyệt (sau khi mọi yêu cầu có quyết định). SSE như /chat."""
    prepared = await c.runs.prepare_resume(p, thread_id)
    return StreamingResponse(
        _sse(c.runs.stream(prepared), prepared),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


class ApprovalOut(BaseModel):
    id: UUID
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
    can_decide: bool


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=500)


def _approval_out(c: Any, p: Principal, a: Approval) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        thread_id=a.thread_id,
        run_id=a.run_id,
        agent_id=a.agent_id,
        requester_id=a.requester_id,
        tool_use_id=a.tool_use_id,
        tool=a.tool,
        risk=a.risk,
        reason=a.reason,
        arguments=a.arguments,
        approver_roles=a.approver_roles,
        connection_id=a.connection_id,
        status=a.status,
        decided_by=a.decided_by,
        decision_note=a.decision_note,
        created_at=a.created_at,
        decided_at=a.decided_at,
        can_decide=a.status is ApprovalStatus.PENDING and c.approvals.can_decide(p, a),
    )


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    p: AnyUser,
    c: ContainerDep,
    status: Annotated[ApprovalStatus | None, Query()] = None,
    thread_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ApprovalOut]:
    items = await c.approvals.list(p, status=status, thread_id=thread_id, limit=limit)
    return [_approval_out(c, p, a) for a in items]


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalOut)
async def decide_approval(
    approval_id: UUID, body: DecisionIn, p: AnyUser, c: ContainerDep
) -> ApprovalOut:
    decided = await c.approvals.decide(p, approval_id, approve=body.approve, note=body.note)
    return _approval_out(c, p, decided)


class PendingOut(BaseModel):
    waiting: bool
    can_resume: bool
    approvals: list[ApprovalOut]


@router.get("/threads/{thread_id}/pending", response_model=PendingOut)
async def thread_pending(thread_id: UUID, p: AnyUser, c: ContainerDep) -> PendingOut:
    """Hội thoại có đang chờ duyệt không (gồm cả yêu cầu của agent con khi chat với Master)."""
    state = await c.runs.pending_state(p, thread_id)
    return PendingOut(
        waiting=state.waiting,
        can_resume=state.can_resume,
        approvals=[_approval_out(c, p, a) for a in state.approvals],
    )


class MasterOut(BaseModel):
    agent_id: UUID
    name: str
    members: int


@router.get("/master", response_model=MasterOut)
async def master(p: AnyUser, c: ContainerDep) -> MasterOut:
    """ARIES Master của tổ chức (tự tạo lần đầu) — đầu mối giao việc cho các agent khác."""
    providers = set(c.model_router.providers)
    model = ("anthropic", "claude-opus-5-5") if "anthropic" in providers else ("fake", "fake-echo")
    agent = await c.agents.ensure_master(p, provider=model[0], model=model[1])
    count = len([a for a in await c.agents.list_agents(p) if a.id != agent.id])
    return MasterOut(agent_id=agent.id, name=agent.name, members=count)


# ---------------------------------------------------------------- feedback (ADR-0020)
class FeedbackIn(BaseModel):
    rating: int = Field(ge=-1, le=1)
    comment: str = Field(default="", max_length=1000)


class FeedbackOut(BaseModel):
    message_id: int
    rating: int
    comment: str


@router.post("/threads/{thread_id}/messages/{message_id}/feedback", response_model=FeedbackOut)
async def give_feedback(
    thread_id: UUID, message_id: int, body: FeedbackIn, p: AnyUser, c: ContainerDep
) -> FeedbackOut:
    f = await c.conversations.give_feedback(
        p, thread_id, message_id, rating=body.rating, comment=body.comment
    )
    return FeedbackOut(message_id=f.message_id, rating=f.rating, comment=f.comment)


@router.get("/threads/{thread_id}/feedback", response_model=list[FeedbackOut])
async def thread_feedback(thread_id: UUID, p: AnyUser, c: ContainerDep) -> list[FeedbackOut]:
    await c.conversations.owned_thread(p, thread_id)
    items = await c.conversations.repo.feedback_for_thread(p.tenant_id, thread_id)
    return [
        FeedbackOut(message_id=f.message_id, rating=f.rating, comment=f.comment)
        for f in items
        if f.user_id == p.subject
    ]
