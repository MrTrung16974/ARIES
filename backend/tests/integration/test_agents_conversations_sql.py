"""Repository SQL của agents/conversations trên Postgres thật (RLS, version immutable, append-only)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aiagent.adapters.db import Database
from aiagent.modules.agents.domain import AgentSpec, CreatedVia
from aiagent.modules.agents.infrastructure.sql_agent_repository import SqlAgentRepository
from aiagent.modules.conversations.domain import RunStatus
from aiagent.modules.conversations.infrastructure.sql_conversation_repository import (
    SqlConversationRepository,
)
from aiagent.shared_kernel.errors import ConflictError
from aiagent.shared_kernel.usage import Usage

pytestmark = pytest.mark.integration
DB_URL = os.environ.get("AIAGENT_TEST_DATABASE_URL")


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    if not DB_URL:
        pytest.skip("Chưa đặt AIAGENT_TEST_DATABASE_URL")
    database = Database(DB_URL, pool_size=2)
    yield database
    await database.dispose()


def spec(name: str = "IT agent") -> AgentSpec:
    return AgentSpec.model_validate(
        {"name": name, "model": {"provider": "fake", "model": "fake-echo"},
         "skills": [{"skill_id": "echo"}]}
    )


async def test_agent_versions_publish_and_isolation(db: Database) -> None:
    repo = SqlAgentRepository(db)
    agent, v1 = await repo.create("default", "it-owner", spec(), CreatedVia.API, "tạo")
    assert (agent.latest_version, v1.version) == (1, 1)
    assert v1.spec.skills[0].skill_id == "echo"

    v2 = await repo.add_version("default", agent.id, spec("v2"), "it-owner", CreatedVia.FORM,
                                "sửa", expected_latest=1)
    assert v2.version == 2
    with pytest.raises(ConflictError):
        await repo.add_version("default", agent.id, spec("stale"), "it-owner", CreatedVia.FORM,
                               "", expected_latest=1)

    published = await repo.set_published("default", agent.id, 1)
    assert (published.published_version, published.status.value) == (1, "published")
    assert [v.version for v in await repo.list_versions("default", agent.id)] == [2, 1]

    assert await repo.get("other-tenant", agent.id) is None
    assert all(a.id != agent.id for a in await repo.list_all("other-tenant"))

    with pytest.raises(DBAPIError):  # version là immutable với role ứng dụng
        async with db.tenant_session("default") as s:
            await s.execute(
                text("UPDATE agents.agent_versions SET change_note = 'x' WHERE agent_id = :id"),
                {"id": agent.id},
            )


async def test_conversation_history_and_runs(db: Database) -> None:
    agents = SqlAgentRepository(db)
    conv = SqlConversationRepository(db)
    agent, _ = await agents.create("default", "it-owner", spec(), CreatedVia.API, "")

    thread = await conv.create_thread("default", "it-user", agent.id, 1, False, "xin chào")
    run = await conv.start_run("default", thread.id, agent.id, 1, "it-user", "fake-echo")
    content = [{"type": "text", "text": "Chào bạn 👋"}]
    await conv.append_message("default", thread.id, run.id, "user", content)
    await conv.append_message("default", thread.id, run.id, "assistant",
                              [{"type": "thinking", "thinking": "", "signature": "sig"},
                               {"type": "text", "text": "Xin chào!"}])
    await conv.finish_run("default", run.id, RunStatus.COMPLETED, Usage(10, 5), 0.0001, None)

    messages = await conv.list_messages("default", thread.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == content
    assert messages[1].content[0]["signature"] == "sig"  # block giữ nguyên để echo lại

    threads = await conv.list_threads("default", "it-user", agent.id, 10)
    assert [t.id for t in threads] == [thread.id]
    assert await conv.get_thread("other-tenant", thread.id) is None

    with pytest.raises(DBAPIError):  # lịch sử append-only
        async with db.tenant_session("default") as s:
            await s.execute(text("DELETE FROM conversations.messages WHERE thread_id = :t"),
                            {"t": thread.id})
