"""API tự động hóa (ADR-0021): /api/v1/schedules."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.automation.domain import DEFAULT_TZ, Schedule, ScheduleRun
from aiagent.modules.identity.domain import Principal

router = APIRouter(prefix="/api/v1", tags=["automation"])

AnyUser = Annotated[Principal, Depends(get_principal)]


class ScheduleOut(BaseModel):
    id: UUID
    owner_id: str
    agent_id: UUID
    name: str
    kind: str
    cron: str
    timezone: str
    prompt: str
    connection_id: UUID | None
    trigger_config: dict[str, Any]
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    failure_count: int
    created_at: datetime


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    agent_id: UUID
    kind: str = "cron"
    prompt: str = Field(min_length=1, max_length=4000)
    cron: str = Field(default="", max_length=100)
    timezone: str = Field(default=DEFAULT_TZ, max_length=64)
    interval_minutes: int = 5
    connection_id: UUID | None = None
    trigger_config: dict[str, Any] = Field(default_factory=dict)


class SchedulePatch(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, max_length=100)
    prompt: str | None = Field(default=None, max_length=4000)
    cron: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=64)
    trigger_config: dict[str, Any] | None = None


class ScheduleRunOut(BaseModel):
    id: UUID
    thread_id: UUID | None
    status: str
    trigger_ref: str
    title: str
    error: str | None
    started_at: datetime
    finished_at: datetime | None


def _out(s: Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=s.id,
        owner_id=s.owner_id,
        agent_id=s.agent_id,
        name=s.name,
        kind=s.kind,
        cron=s.cron,
        timezone=s.timezone,
        prompt=s.prompt,
        connection_id=s.connection_id,
        trigger_config=s.trigger_config,
        enabled=s.enabled,
        next_run_at=s.next_run_at,
        last_run_at=s.last_run_at,
        last_status=s.last_status,
        failure_count=s.failure_count,
        created_at=s.created_at,
    )


def _run_out(r: ScheduleRun) -> ScheduleRunOut:
    return ScheduleRunOut(
        id=r.id,
        thread_id=r.thread_id,
        status=r.status,
        trigger_ref=r.trigger_ref,
        title=r.title,
        error=r.error,
        started_at=r.started_at,
        finished_at=r.finished_at,
    )


@router.get("/schedules", response_model=list[ScheduleOut])
async def list_schedules(
    principal: AnyUser, c: ContainerDep, everyone: Annotated[bool, Query()] = False
) -> list[ScheduleOut]:
    return [_out(s) for s in await c.automation.list_schedules(principal, everyone=everyone)]


@router.post("/schedules", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleIn, principal: AnyUser, c: ContainerDep) -> ScheduleOut:
    s = await c.automation.create(
        principal,
        name=body.name,
        agent_id=body.agent_id,
        kind=body.kind,
        prompt=body.prompt,
        cron=body.cron,
        timezone=body.timezone,
        interval_minutes=body.interval_minutes,
        connection_id=body.connection_id,
        trigger_config=body.trigger_config,
    )
    return _out(s)


@router.get("/schedules/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(schedule_id: UUID, principal: AnyUser, c: ContainerDep) -> ScheduleOut:
    return _out(await c.automation.get(principal, schedule_id))


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: UUID, body: SchedulePatch, principal: AnyUser, c: ContainerDep
) -> ScheduleOut:
    s = await c.automation.update(
        principal,
        schedule_id,
        enabled=body.enabled,
        name=body.name,
        prompt=body.prompt,
        cron=body.cron,
        timezone=body.timezone,
        trigger_config=body.trigger_config,
    )
    return _out(s)


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(schedule_id: UUID, principal: AnyUser, c: ContainerDep) -> Response:
    await c.automation.delete(principal, schedule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/schedules/{schedule_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_now(schedule_id: UUID, principal: AnyUser, c: ContainerDep) -> dict[str, bool]:
    await c.automation.run_now(principal, schedule_id)
    return {"queued": True}


@router.get("/schedules/{schedule_id}/runs", response_model=list[ScheduleRunOut])
async def list_runs(
    schedule_id: UUID,
    principal: AnyUser,
    c: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ScheduleRunOut]:
    return [_run_out(r) for r in await c.automation.runs(principal, schedule_id, limit)]
