"""Repository SQL của builder trên Postgres thật (RLS, append-only)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aiagent.adapters.db import Database
from aiagent.modules.builder.domain import SessionStatus
from aiagent.modules.builder.infrastructure.sql_session_repository import (
    SqlBuilderSessionRepository,
)

pytestmark = pytest.mark.integration
DB_URL = os.environ.get("AIAGENT_TEST_DATABASE_URL")


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    if not DB_URL:
        pytest.skip("Chưa đặt AIAGENT_TEST_DATABASE_URL")
    database = Database(DB_URL, pool_size=2)
    yield database
    await database.dispose()


async def test_session_roundtrip_isolation_and_append_only(db: Database) -> None:
    repo = SqlBuilderSessionRepository(db)
    session = await repo.create("default", "it-builder", None, None, None)
    assert session.status is SessionStatus.DRAFTING and session.questions == []

    session.brief = {"goal": "báo giờ", "assumptions": ["tiếng Việt"]}
    session.draft_spec = {"name": "Trợ lý giờ", "rules": ["Không nói tục"]}
    session.questions = [{"id": "q1", "question": "Ai dùng?", "options": []}]
    session.clarify_rounds = 1
    session.eval_report = {"score": 1.0, "passed": True, "results": [], "threshold": 0.8}
    session.status = SessionStatus.READY
    await repo.save(session)
    await repo.add_message("default", session.id, "user", "Tạo trợ lý báo giờ 👋", [])
    await repo.add_message("default", session.id, "architect", "Đã dựng", [{"type": "spec_draft"}])

    loaded = await repo.get("default", session.id)
    assert loaded is not None
    assert loaded.status is SessionStatus.READY and loaded.clarify_rounds == 1
    assert loaded.draft_spec == session.draft_spec and loaded.eval_report["passed"] is True
    history = await repo.messages("default", session.id)
    assert [m["role"] for m in history] == ["user", "architect"]
    assert history[0]["content"] == "Tạo trợ lý báo giờ 👋"
    assert history[1]["events"] == [{"type": "spec_draft"}]

    assert await repo.get("other-tenant", session.id) is None
    mine = await repo.list_for_user("default", "it-builder", 50)
    assert session.id in {s.id for s in mine}

    with pytest.raises(DBAPIError):
        async with db.tenant_session("default") as s:
            await s.execute(
                text("DELETE FROM builder.messages WHERE session_id = :id"), {"id": session.id}
            )
