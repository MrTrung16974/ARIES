from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, get_principal
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
