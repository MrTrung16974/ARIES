"""Chạy với Postgres thật đã migrate, kết nối bằng role ứng dụng `aiagent_app`.

AIAGENT_TEST_DATABASE_URL=postgresql+asyncpg://aiagent_app:...@db/aiagent pytest -m integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aiagent.adapters.db import Database
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.audit.infrastructure.sql_audit_log import SqlAuditLog
from aiagent.modules.identity.domain import Principal, Role
from aiagent.modules.identity.infrastructure.sql_user_repository import SqlUserRepository

pytestmark = pytest.mark.integration

DB_URL = os.environ.get("AIAGENT_TEST_DATABASE_URL")


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    if not DB_URL:
        pytest.skip("Chưa đặt AIAGENT_TEST_DATABASE_URL")
    database = Database(DB_URL, pool_size=2)
    yield database
    await database.dispose()


async def test_app_role_cannot_bypass_rls(db: Database) -> None:
    async with db.engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
    assert row.rolsuper is False
    assert row.rolbypassrls is False


async def test_users_are_isolated_by_tenant(db: Database) -> None:
    repo = SqlUserRepository(db)
    subject = f"it-{uuid.uuid4()}"
    await repo.touch(Principal(subject, "default", "it", None, frozenset({Role.USER})))

    async with db.tenant_session("default") as s:
        seen = (
            await s.execute(
                text("SELECT count(*) FROM identity.users WHERE subject = :s"), {"s": subject}
            )
        ).scalar_one()
    async with db.tenant_session("other-tenant") as s:
        hidden = (
            await s.execute(
                text("SELECT count(*) FROM identity.users WHERE subject = :s"), {"s": subject}
            )
        ).scalar_one()
    assert (seen, hidden) == (1, 0)


async def test_cannot_write_into_another_tenant(db: Database) -> None:
    with pytest.raises(DBAPIError):
        async with db.tenant_session("default") as s:
            await s.execute(
                text("INSERT INTO identity.users (tenant_id, subject) VALUES ('other-tenant', 'x')")
            )


async def test_audit_chain_and_append_only(db: Database) -> None:
    svc = AuditService(SqlAuditLog(db))
    rec = await svc.record(
        tenant_id="default",
        actor_id="it",
        action="it.test",
        outcome=Outcome.SUCCESS,
        details={"password": "should-not-be-stored"},
    )
    assert rec.event.details == {"password": "***"}
    assert (await svc.verify_chain("default")).intact

    with pytest.raises(DBAPIError):
        async with db.tenant_session("default") as s:
            await s.execute(
                text("UPDATE audit.audit_events SET action = 'x' WHERE seq = :seq"),
                {"seq": rec.seq},
            )
    with pytest.raises(DBAPIError):
        async with db.tenant_session("default") as s:
            await s.execute(
                text("DELETE FROM audit.audit_events WHERE seq = :seq"), {"seq": rec.seq}
            )
