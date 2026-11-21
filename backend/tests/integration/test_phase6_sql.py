"""Phase 6 trên Postgres thật: lịch chạy, nhận lịch đến hạn (SKIP LOCKED), lịch sử chạy (ADR-0021)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

import pytest

from aiagent.adapters.db import Database
from aiagent.modules.agents.domain import AgentSpec, CreatedVia
from aiagent.modules.agents.infrastructure.sql_agent_repository import SqlAgentRepository
from aiagent.modules.automation.domain import Schedule
from aiagent.modules.automation.infrastructure import SqlScheduleRepository, SqlTenantList
from aiagent.shared_kernel.clock import utcnow

pytestmark = pytest.mark.integration
DB_URL = os.environ.get("AIAGENT_TEST_DATABASE_URL")


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    if not DB_URL:
        pytest.skip("Chưa đặt AIAGENT_TEST_DATABASE_URL")
    database = Database(DB_URL, pool_size=4)
    yield database
    await database.dispose()


async def test_schedule_claim_runs_and_rls(db: Database) -> None:
    spec = AgentSpec.model_validate(
        {"name": f"P6 {uuid4().hex[:6]}", "model": {"provider": "fake", "model": "fake-echo"}}
    )
    agent, _ = await SqlAgentRepository(db).create("default", "it-user", spec, CreatedVia.API, "")
    repo = SqlScheduleRepository(db)
    now = utcnow()
    s = Schedule(
        id=uuid4(),
        tenant_id="default",
        owner_id=f"it-{uuid4().hex[:6]}",
        agent_id=agent.id,
        name="Báo cáo",
        kind="cron",
        cron="0 8 * * 1",
        timezone="Asia/Ho_Chi_Minh",
        prompt="Tổng hợp",
        connection_id=None,
        trigger_config={"from_contains": "ncc.vn"},
        state={},
        enabled=True,
        next_run_at=now - timedelta(minutes=1),
        last_run_at=None,
        last_status=None,
        failure_count=0,
        created_at=now,
        updated_at=now,
    )
    created = await repo.create(s)
    assert created.trigger_config == {"from_contains": "ncc.vn"}
    assert await repo.count_for_owner("default", s.owner_id) == 1
    assert "default" in await SqlTenantList(db).tenant_ids()

    # Hai worker nhận cùng lúc → chỉ một bên nhận được (FOR UPDATE SKIP LOCKED)
    later = now + timedelta(days=7)
    a, b = await asyncio.gather(
        repo.claim_due("default", now, lambda _: later),
        repo.claim_due("default", now, lambda _: later),
    )
    mine = [d for d in a + b if d.schedule_id == s.id]
    assert len(mine) == 1
    got = await repo.get("default", s.id)
    assert got is not None and got.next_run_at == later
    assert [
        d for d in await repo.claim_due("default", now, lambda _: later) if d.schedule_id == s.id
    ] == []

    run = await repo.start_run("default", s.id, "2026-11-23T01:00", "Báo cáo")
    assert run is not None and run.status == "running"
    assert await repo.start_run("default", s.id, "2026-11-23T01:00", "lặp") is None  # UNIQUE
    await repo.finish_run("default", run.id, status="completed", thread_id=None, error=None)
    runs = await repo.list_runs("default", s.id, 10)
    assert runs[0].status == "completed" and runs[0].finished_at is not None

    await repo.record_result(
        "default", s.id, status="failed", failed=True, state={"h": "9"}, disable=False
    )
    got = await repo.get("default", s.id)
    assert got is not None and got.failure_count == 1 and got.state == {"h": "9"}
    await repo.record_result(
        "default", s.id, status="failed", failed=True, state=None, disable=True
    )
    got = await repo.get("default", s.id)
    assert (
        got is not None and not got.enabled and got.next_run_at is None and got.state == {"h": "9"}
    )

    assert await repo.get("other-tenant", s.id) is None
    assert await repo.list_for("other-tenant", None) == []
    await repo.delete("default", s.id)
    assert await repo.list_runs("default", s.id, 10) == []  # ON DELETE CASCADE
