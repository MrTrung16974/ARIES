"""Adapter tự động hóa: Postgres (RLS), danh sách tenant, hàng đợi arq, nguồn trigger, runner."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aiagent.adapters.db import Database
from aiagent.modules.automation.domain import (
    Due,
    RunOutcome,
    Schedule,
    ScheduleRun,
    TriggerItem,
    WaitingRun,
)
from aiagent.modules.connections.application import ConnectionService
from aiagent.modules.identity.domain import Principal
from aiagent.modules.runtime.application import RunAgentService
from aiagent.plugin_sdk import Plugin
from aiagent.shared_kernel.errors import AppError, NotFoundError

_SELECT = """SELECT id, tenant_id, owner_id, agent_id, name, kind, cron, timezone, prompt,
       connection_id, trigger_config, state, enabled, next_run_at, last_run_at, last_status,
       failure_count, created_at, updated_at FROM automation.schedules """
_INSERT = text(
    """
    INSERT INTO automation.schedules
      (id, tenant_id, owner_id, agent_id, name, kind, cron, timezone, prompt, connection_id,
       trigger_config, state, enabled, next_run_at)
    VALUES (:id, :tenant_id, :owner_id, :agent_id, :name, :kind, :cron, :timezone, :prompt,
            :connection_id, CAST(:trigger_config AS jsonb), CAST(:state AS jsonb), :enabled,
            :next_run_at)
    """
)
_GET = text(_SELECT + "WHERE id = :id")
_LIST = text(
    _SELECT + "WHERE (CAST(:owner AS text) IS NULL OR owner_id = CAST(:owner AS text)) "
    "ORDER BY created_at DESC"
)
_COUNT = text("SELECT count(*) FROM automation.schedules WHERE owner_id = :owner")
_SAVE = text(
    """
    UPDATE automation.schedules
       SET name = :name, prompt = :prompt, cron = :cron, timezone = :timezone,
           trigger_config = CAST(:trigger_config AS jsonb), enabled = :enabled,
           next_run_at = :next_run_at, failure_count = :failure_count, updated_at = now()
     WHERE id = :id
    """
)
_DELETE = text("DELETE FROM automation.schedules WHERE id = :id")
_DUE = text(
    _SELECT + "WHERE enabled AND next_run_at <= :now ORDER BY next_run_at LIMIT 100 "
    "FOR UPDATE SKIP LOCKED"
)
_SET_NEXT = text(
    "UPDATE automation.schedules SET next_run_at = :next, enabled = :enabled WHERE id = :id"
)
_RESULT = text(
    """
    UPDATE automation.schedules
       SET last_run_at = now(), last_status = :status,
           failure_count = CASE WHEN :failed THEN failure_count + 1 ELSE 0 END,
           state = COALESCE(CAST(:state AS jsonb), state),
           enabled = enabled AND NOT :disable,
           next_run_at = CASE WHEN :disable THEN NULL ELSE next_run_at END
     WHERE id = :id
    """
)
_START_RUN = text(
    """
    INSERT INTO automation.schedule_runs (id, tenant_id, schedule_id, status, trigger_ref, title)
    VALUES (:id, :tenant_id, :schedule_id, 'running', :ref, :title)
    ON CONFLICT (schedule_id, trigger_ref) DO NOTHING
    RETURNING id, schedule_id, thread_id, status, trigger_ref, title, error, started_at,
              finished_at
    """
)
_FINISH_RUN = text(
    """
    UPDATE automation.schedule_runs
       SET status = :status, thread_id = :thread_id, error = :error, finished_at = now()
     WHERE id = :id
    """
)
_WAITING = text(
    """
    SELECT r.id, r.schedule_id, r.thread_id, s.owner_id
      FROM automation.schedule_runs r
      JOIN automation.schedules s ON s.id = r.schedule_id
     WHERE r.status = 'awaiting_approval' AND r.thread_id IS NOT NULL
       AND r.started_at > now() - interval '7 days'
     ORDER BY r.started_at
     LIMIT 50
    """
)
_LIST_RUNS = text(
    """
    SELECT id, schedule_id, thread_id, status, trigger_ref, title, error, started_at, finished_at
      FROM automation.schedule_runs WHERE schedule_id = :sid ORDER BY started_at DESC LIMIT :limit
    """
)


def _schedule(m: Any) -> Schedule:
    return Schedule(
        id=m["id"],
        tenant_id=m["tenant_id"],
        owner_id=m["owner_id"],
        agent_id=m["agent_id"],
        name=m["name"],
        kind=m["kind"],
        cron=m["cron"],
        timezone=m["timezone"],
        prompt=m["prompt"],
        connection_id=m["connection_id"],
        trigger_config=dict(m["trigger_config"] or {}),
        state=dict(m["state"] or {}),
        enabled=bool(m["enabled"]),
        next_run_at=m["next_run_at"],
        last_run_at=m["last_run_at"],
        last_status=m["last_status"],
        failure_count=int(m["failure_count"]),
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def _run(m: Any) -> ScheduleRun:
    return ScheduleRun(
        id=m["id"],
        schedule_id=m["schedule_id"],
        thread_id=m["thread_id"],
        status=m["status"],
        trigger_ref=m["trigger_ref"],
        title=m["title"],
        error=m["error"],
        started_at=m["started_at"],
        finished_at=m["finished_at"],
    )


class SqlScheduleRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, s: Schedule) -> Schedule:
        async with self._db.tenant_session(s.tenant_id) as session:
            await session.execute(
                _INSERT,
                {
                    "id": s.id,
                    "tenant_id": s.tenant_id,
                    "owner_id": s.owner_id,
                    "agent_id": s.agent_id,
                    "name": s.name,
                    "kind": s.kind,
                    "cron": s.cron,
                    "timezone": s.timezone,
                    "prompt": s.prompt,
                    "connection_id": s.connection_id,
                    "trigger_config": json.dumps(s.trigger_config, ensure_ascii=False),
                    "state": json.dumps(s.state),
                    "enabled": s.enabled,
                    "next_run_at": s.next_run_at,
                },
            )
        found = await self.get(s.tenant_id, s.id)
        if found is None:
            raise NotFoundError()
        return found

    async def get(self, tenant_id: str, schedule_id: UUID) -> Schedule | None:
        async with self._db.tenant_session(tenant_id) as session:
            row = (await session.execute(_GET, {"id": schedule_id})).first()
        return _schedule(row._mapping) if row else None

    async def list_for(self, tenant_id: str, owner_id: str | None) -> list[Schedule]:
        async with self._db.tenant_session(tenant_id) as session:
            rows = (await session.execute(_LIST, {"owner": owner_id})).all()
        return [_schedule(r._mapping) for r in rows]

    async def count_for_owner(self, tenant_id: str, owner_id: str) -> int:
        async with self._db.tenant_session(tenant_id) as session:
            return int((await session.execute(_COUNT, {"owner": owner_id})).scalar_one())

    async def save(self, s: Schedule) -> Schedule:
        async with self._db.tenant_session(s.tenant_id) as session:
            await session.execute(
                _SAVE,
                {
                    "id": s.id,
                    "name": s.name,
                    "prompt": s.prompt,
                    "cron": s.cron,
                    "timezone": s.timezone,
                    "trigger_config": json.dumps(s.trigger_config, ensure_ascii=False),
                    "enabled": s.enabled,
                    "next_run_at": s.next_run_at,
                    "failure_count": s.failure_count,
                },
            )
        found = await self.get(s.tenant_id, s.id)
        if found is None:
            raise NotFoundError()
        return found

    async def delete(self, tenant_id: str, schedule_id: UUID) -> None:
        async with self._db.tenant_session(tenant_id) as session:
            await session.execute(_DELETE, {"id": schedule_id})

    async def claim_due(
        self, tenant_id: str, now: datetime, next_of: Callable[[Schedule], datetime | None]
    ) -> list[Due]:
        claimed: list[Due] = []
        async with self._db.tenant_session(tenant_id) as session:
            rows = (await session.execute(_DUE, {"now": now})).all()
            for r in rows:
                s = _schedule(r._mapping)
                nxt = next_of(s)
                await session.execute(
                    _SET_NEXT, {"id": s.id, "next": nxt, "enabled": nxt is not None}
                )
                if s.next_run_at is not None:
                    claimed.append(Due(tenant_id, s.id, s.next_run_at))
        return claimed

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
        async with self._db.tenant_session(tenant_id) as session:
            await session.execute(
                _RESULT,
                {
                    "id": schedule_id,
                    "status": status,
                    "failed": failed,
                    "state": json.dumps(state) if state is not None else None,
                    "disable": disable,
                },
            )

    async def start_run(
        self, tenant_id: str, schedule_id: UUID, trigger_ref: str, title: str
    ) -> ScheduleRun | None:
        try:
            async with self._db.tenant_session(tenant_id) as session:
                row = (
                    await session.execute(
                        _START_RUN,
                        {
                            "id": uuid4(),
                            "tenant_id": tenant_id,
                            "schedule_id": schedule_id,
                            "ref": trigger_ref[:200],
                            "title": title[:200],
                        },
                    )
                ).first()
        except IntegrityError:  # schedule vừa bị xóa
            return None
        return _run(row._mapping) if row else None

    async def finish_run(
        self,
        tenant_id: str,
        run_id: UUID,
        *,
        status: str,
        thread_id: UUID | None,
        error: str | None,
    ) -> None:
        async with self._db.tenant_session(tenant_id) as session:
            await session.execute(
                _FINISH_RUN,
                {"id": run_id, "status": status, "thread_id": thread_id, "error": error},
            )

    async def list_runs(self, tenant_id: str, schedule_id: UUID, limit: int) -> list[ScheduleRun]:
        async with self._db.tenant_session(tenant_id) as session:
            rows = (await session.execute(_LIST_RUNS, {"sid": schedule_id, "limit": limit})).all()
        return [_run(r._mapping) for r in rows]

    async def waiting_runs(self, tenant_id: str) -> list[WaitingRun]:
        async with self._db.tenant_session(tenant_id) as session:
            rows = (await session.execute(_WAITING)).all()
        return [WaitingRun(r.id, r.schedule_id, r.thread_id, r.owner_id) for r in rows]


class SqlTenantList:
    """`identity.tenants` không có RLS (danh mục tenant) — worker duyệt từng tenant."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def tenant_ids(self) -> list[str]:
        async with self._db.engine.connect() as conn:
            rows = (await conn.execute(text("SELECT id FROM identity.tenants ORDER BY id"))).all()
        return [str(r[0]) for r in rows]


class ArqScheduleQueue:
    def __init__(self, redis_dsn: str) -> None:
        self._dsn = redis_dsn
        self._pool: Any = None

    async def enqueue_schedule(self, tenant_id: str, schedule_id: UUID, fired_at: datetime) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        if self._pool is None:
            self._pool = await create_pool(RedisSettings.from_dsn(self._dsn))
        # _job_id cố định theo lần kích hoạt → không đẩy trùng job nếu tick chạy lặp.
        await self._pool.enqueue_job(
            "run_schedule",
            tenant_id,
            str(schedule_id),
            fired_at.isoformat(),
            _job_id=f"schedule:{schedule_id}:{fired_at.isoformat()}",
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()


class PluginTriggerSource:
    """Mở connection qua Credential Broker (kiểm quyền chủ schedule) rồi gọi plugin.poll_trigger."""

    def __init__(self, connections: ConnectionService, plugins: Any) -> None:
        self._connections = connections
        self._plugins = plugins

    async def poll(
        self,
        principal: Principal,
        connection_id: UUID,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[list[TriggerItem], dict[str, Any]]:
        ctx = await self._connections.open(principal, str(connection_id))
        plugin: Plugin | None = self._plugins.get(ctx.connector_id)
        if plugin is None or not plugin.manifest.triggers:
            raise AppError("Connector không hỗ trợ trigger.")
        trigger = plugin.manifest.triggers[0].name
        events, new_state = await plugin.poll_trigger(trigger, ctx, state, config)
        return [TriggerItem(e.id, e.title, e.content) for e in events], new_state


class RuntimeRunner:
    def __init__(self, runs: RunAgentService) -> None:
        self._runs = runs

    async def run_once(
        self, principal: Principal, agent_id: UUID, message: str, title: str
    ) -> RunOutcome:
        done = await self._runs.run_once(principal, agent_id, message, title=title)
        return RunOutcome(done.thread_id, done.status, done.text)

    async def resume_once(self, principal: Principal, thread_id: UUID) -> RunOutcome:
        done = await self._runs.resume_once(principal, thread_id)
        return RunOutcome(done.thread_id, done.status, done.text)
