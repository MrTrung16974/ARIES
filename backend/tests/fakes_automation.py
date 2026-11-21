"""Fake cho module tự động hóa (ADR-0021)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from aiagent.modules.automation.domain import Due, Schedule, ScheduleRun, TriggerItem
from aiagent.shared_kernel.clock import utcnow


class InMemoryScheduleRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, Schedule] = {}
        self.runs: list[ScheduleRun] = []

    async def create(self, s: Schedule) -> Schedule:
        self.items[s.id] = s
        return s

    async def get(self, tenant_id: str, schedule_id: UUID) -> Schedule | None:
        s = self.items.get(schedule_id)
        return s if s and s.tenant_id == tenant_id else None

    async def list_for(self, tenant_id: str, owner_id: str | None) -> list[Schedule]:
        return [
            s
            for s in self.items.values()
            if s.tenant_id == tenant_id and (owner_id is None or s.owner_id == owner_id)
        ]

    async def count_for_owner(self, tenant_id: str, owner_id: str) -> int:
        return len(await self.list_for(tenant_id, owner_id))

    async def save(self, s: Schedule) -> Schedule:
        self.items[s.id] = s
        return s

    async def delete(self, tenant_id: str, schedule_id: UUID) -> None:
        self.items.pop(schedule_id, None)
        self.runs = [r for r in self.runs if r.schedule_id != schedule_id]

    async def claim_due(
        self, tenant_id: str, now: datetime, next_of: Callable[[Schedule], datetime | None]
    ) -> list[Due]:
        out = []
        for s in list(self.items.values()):
            if s.tenant_id == tenant_id and s.enabled and s.next_run_at and s.next_run_at <= now:
                nxt = next_of(s)
                self.items[s.id] = replace(s, next_run_at=nxt, enabled=nxt is not None)
                out.append(Due(tenant_id, s.id, s.next_run_at))
        return out

    async def record_result(
        self,
        tenant_id: str,
        schedule_id: UUID,
        *,
        status: str,
        failed: bool,
        state: dict[str, Any] | None,
        disable: bool,
    ) -> None:
        s = self.items[schedule_id]
        self.items[schedule_id] = replace(
            s,
            last_run_at=utcnow(),
            last_status=status,
            failure_count=s.failure_count + 1 if failed else 0,
            state=state if state is not None else s.state,
            enabled=s.enabled and not disable,
            next_run_at=None if disable else s.next_run_at,
        )

    async def start_run(
        self, tenant_id: str, schedule_id: UUID, trigger_ref: str, title: str
    ) -> ScheduleRun | None:
        if schedule_id not in self.items or any(
            r.schedule_id == schedule_id and r.trigger_ref == trigger_ref for r in self.runs
        ):
            return None
        run = ScheduleRun(
            uuid4(), schedule_id, None, "running", trigger_ref, title, None, utcnow(), None
        )
        self.runs.append(run)
        return run

    async def finish_run(
        self,
        tenant_id: str,
        run_id: UUID,
        *,
        status: str,
        thread_id: UUID | None,
        error: str | None,
    ) -> None:
        self.runs = [
            replace(r, status=status, thread_id=thread_id, error=error, finished_at=utcnow())
            if r.id == run_id
            else r
            for r in self.runs
        ]

    async def list_runs(self, tenant_id: str, schedule_id: UUID, limit: int) -> list[ScheduleRun]:
        return [r for r in reversed(self.runs) if r.schedule_id == schedule_id][:limit]


class StaticTenants:
    async def tenant_ids(self) -> list[str]:
        return ["default"]


class RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, UUID, datetime]] = []

    async def enqueue_schedule(self, tenant_id: str, schedule_id: UUID, fired_at: datetime) -> None:
        self.jobs.append((tenant_id, schedule_id, fired_at))


class FakeTriggerSource:
    """Trả lần lượt các lô sự kiện đã nạp; ghi lại state nhận được."""

    def __init__(self) -> None:
        self.batches: list[list[TriggerItem]] = []
        self.seen_states: list[dict[str, Any]] = []
        self.fail = False

    async def poll(
        self, principal: Any, connection_id: UUID, state: dict[str, Any], config: dict[str, Any]
    ) -> tuple[list[TriggerItem], dict[str, Any]]:
        if self.fail:
            raise ConnectionError("Gmail lỗi")
        self.seen_states.append(dict(state))
        batch = self.batches.pop(0) if self.batches else []
        return batch, {"history_id": str(int(state.get("history_id", "0")) + 1)}


async def _waiting_runs(self: InMemoryScheduleRepository, tenant_id: str) -> list[Any]:
    from aiagent.modules.automation.domain import WaitingRun

    return [
        WaitingRun(r.id, r.schedule_id, r.thread_id, self.items[r.schedule_id].owner_id)
        for r in self.runs
        if r.status == "awaiting_approval"
        and r.thread_id is not None
        and r.schedule_id in self.items
    ]


InMemoryScheduleRepository.waiting_runs = _waiting_runs  # type: ignore[attr-defined]
