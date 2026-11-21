"""API vận hành (ADR-0020): /api/v1/ops/*."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.identity.domain import Principal
from aiagent.modules.ops.domain import OpsFilter, text_of

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

AnyUser = Annotated[Principal, Depends(get_principal)]
AgentQ = Annotated[UUID | None, Query()]
ChannelQ = Annotated[str | None, Query()]
DateQ = Annotated[datetime | None, Query()]


class ThreadSummaryOut(BaseModel):
    id: UUID
    agent_id: UUID
    user_id: str
    channel: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    cost_usd: float
    thumbs_up: int
    thumbs_down: int


class TranscriptMessageOut(BaseModel):
    id: int
    role: str
    created_at: datetime
    parts: list[dict[str, Any]]


class FeedbackOut(BaseModel):
    message_id: int
    user_id: str
    rating: int
    comment: str


class TranscriptOut(BaseModel):
    id: UUID
    agent_id: UUID
    agent_version: int
    user_id: str
    channel: str
    title: str
    created_at: datetime
    messages: list[TranscriptMessageOut]
    feedback: list[FeedbackOut]


class FeedbackItemOut(BaseModel):
    id: UUID
    thread_id: UUID
    message_id: int
    agent_id: UUID
    user_id: str
    channel: str
    rating: int
    comment: str
    created_at: datetime
    answer: str


class UsageRowOut(BaseModel):
    day: date
    agent_id: UUID
    channel: str
    runs: int
    failed_runs: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class UsageReportOut(BaseModel):
    since: datetime
    until: datetime
    runs: int
    failed_runs: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    rows: list[UsageRowOut]


@router.get("/threads", response_model=list[ThreadSummaryOut])
async def list_threads(
    principal: AnyUser,
    container: ContainerDep,
    agent_id: AgentQ = None,
    channel: ChannelQ = None,
    rating: Annotated[int | None, Query()] = None,
    since: DateQ = None,
    until: DateQ = None,
    before: DateQ = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ThreadSummaryOut]:
    f = OpsFilter(agent_id=agent_id, channel=channel, rating=rating, since=since, until=until)
    items = await container.ops.threads(principal, f, limit=limit, before=before)
    return [ThreadSummaryOut(**vars(t)) for t in items]


@router.get("/threads/{thread_id}", response_model=TranscriptOut)
async def get_transcript(
    thread_id: UUID, principal: AnyUser, container: ContainerDep
) -> TranscriptOut:
    t = await container.ops.transcript(principal, thread_id)
    return TranscriptOut(
        id=t.thread.id,
        agent_id=t.thread.agent_id,
        agent_version=t.thread.agent_version,
        user_id=t.thread.user_id,
        channel=t.channel,
        title=t.thread.title,
        created_at=t.thread.created_at,
        messages=[TranscriptMessageOut(**vars(m)) for m in t.messages],
        feedback=[
            FeedbackOut(
                message_id=f.message_id, user_id=f.user_id, rating=f.rating, comment=f.comment
            )
            for f in t.feedback
        ],
    )


@router.get("/feedback", response_model=list[FeedbackItemOut])
async def list_feedback(
    principal: AnyUser,
    container: ContainerDep,
    agent_id: AgentQ = None,
    channel: ChannelQ = None,
    rating: Annotated[int | None, Query()] = None,
    since: DateQ = None,
    until: DateQ = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[FeedbackItemOut]:
    f = OpsFilter(agent_id=agent_id, channel=channel, rating=rating, since=since, until=until)
    items = await container.ops.feedback(principal, f, limit=limit)
    return [FeedbackItemOut(**{**vars(i), "answer": text_of(i.answer)}) for i in items]


@router.get("/usage", response_model=UsageReportOut)
async def usage(
    principal: AnyUser,
    container: ContainerDep,
    agent_id: AgentQ = None,
    channel: ChannelQ = None,
    since: DateQ = None,
    until: DateQ = None,
) -> UsageReportOut:
    r = await container.ops.usage(
        principal, OpsFilter(agent_id=agent_id, channel=channel, since=since, until=until)
    )
    return UsageReportOut(
        since=r.since,
        until=r.until,
        runs=r.runs,
        failed_runs=r.failed_runs,
        input_tokens=r.input_tokens,
        output_tokens=r.output_tokens,
        cost_usd=r.cost_usd,
        rows=[UsageRowOut(**vars(x)) for x in r.rows],
    )
