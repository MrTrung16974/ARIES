"""Domain tự động hóa (ADR-0021): schedule (cron | gmail), lịch sử chạy, cổng ra ngoài."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from aiagent.modules.automation.domain.cron import CronError, CronExpr, validate, zone_of

KINDS = ("cron", "gmail")
GMAIL_INTERVALS = (2, 5, 10, 15, 30, 60)
MAX_SCHEDULES_PER_USER = 20
MAX_FAILURES = 5  # lỗi liên tiếp → tự tắt
DEFAULT_TZ = "Asia/Ho_Chi_Minh"


@dataclass(frozen=True)
class Schedule:
    id: UUID
    tenant_id: str
    owner_id: str
    agent_id: UUID
    name: str
    kind: str  # "cron" | "gmail"
    cron: str
    timezone: str
    prompt: str
    connection_id: UUID | None
    trigger_config: dict[str, Any]  # gmail: {from_contains, subject_contains}
    state: dict[str, Any] = field(repr=False)  # gmail: {history_id}
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    failure_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ScheduleRun:
    id: UUID
    schedule_id: UUID
    thread_id: UUID | None
    status: str  # running | completed | failed | refused | awaiting_approval | skipped
    trigger_ref: str  # thời điểm kích hoạt (cron) hoặc id mail (gmail) — chống chạy lặp
    title: str
    error: str | None
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class Due:
    tenant_id: str
    schedule_id: UUID
    fired_at: datetime


@dataclass(frozen=True)
class TriggerItem:
    """Một sự kiện từ nguồn ngoài (mail mới). `content` là dữ liệu KHÔNG tin cậy."""

    ref: str
    title: str
    content: str


@dataclass(frozen=True)
class RunOutcome:
    thread_id: UUID
    status: str
    text: str


class ScheduleRepositoryPort(Protocol):
    async def create(self, s: Schedule) -> Schedule: ...

    async def get(self, tenant_id: str, schedule_id: UUID) -> Schedule | None: ...

    async def list_for(self, tenant_id: str, owner_id: str | None) -> list[Schedule]:
        """owner_id None = mọi schedule của tenant (admin)."""
        ...

    async def count_for_owner(self, tenant_id: str, owner_id: str) -> int: ...

    async def save(self, s: Schedule) -> Schedule:
        """Cập nhật các trường người dùng sửa + enabled/next_run_at."""
        ...

    async def delete(self, tenant_id: str, schedule_id: UUID) -> None: ...

    async def claim_due(
        self, tenant_id: str, now: datetime, next_of: Callable[[Schedule], datetime | None]
    ) -> list[Due]:
        """Nhận schedule đến hạn (khóa SKIP LOCKED) và ghi next_run_at mới trong cùng transaction."""
        ...

    async def record_result(
        self,
        tenant_id: str,
        schedule_id: UUID,
        *,
        status: str,
        failed: bool,
        state: dict[str, Any] | None,
        disable: bool,
    ) -> None: ...

    async def start_run(
        self, tenant_id: str, schedule_id: UUID, trigger_ref: str, title: str
    ) -> ScheduleRun | None:
        """None nếu trigger_ref này đã chạy (UNIQUE)."""
        ...

    async def finish_run(
        self,
        tenant_id: str,
        run_id: UUID,
        *,
        status: str,
        thread_id: UUID | None,
        error: str | None,
    ) -> None: ...

    async def list_runs(
        self, tenant_id: str, schedule_id: UUID, limit: int
    ) -> list[ScheduleRun]: ...


class TenantListPort(Protocol):
    async def tenant_ids(self) -> list[str]: ...


class JobQueuePort(Protocol):
    async def enqueue_schedule(
        self, tenant_id: str, schedule_id: UUID, fired_at: datetime
    ) -> None: ...


class AgentRunPort(Protocol):
    async def run_once(
        self, principal: Any, agent_id: UUID, message: str, title: str
    ) -> RunOutcome: ...


class TriggerSourcePort(Protocol):
    """Lấy sự kiện mới từ connection (Gmail…) — hiện thực ở bootstrap qua plugin + broker."""

    async def poll(
        self,
        principal: Any,
        connection_id: UUID,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[list[TriggerItem], dict[str, Any]]: ...


__all__ = [
    "DEFAULT_TZ",
    "GMAIL_INTERVALS",
    "KINDS",
    "MAX_FAILURES",
    "MAX_SCHEDULES_PER_USER",
    "AgentRunPort",
    "CronError",
    "CronExpr",
    "Due",
    "JobQueuePort",
    "RunOutcome",
    "Schedule",
    "ScheduleRepositoryPort",
    "ScheduleRun",
    "TenantListPort",
    "TriggerItem",
    "TriggerSourcePort",
    "validate",
    "zone_of",
]
