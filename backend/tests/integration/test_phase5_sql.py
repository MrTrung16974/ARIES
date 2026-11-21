"""Phase 5 trên Postgres thật: kênh publish, feedback, read model vận hành, bot Telegram (ADR-0020)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from aiagent.adapters.db import Database
from aiagent.modules.agents.domain import Agent, AgentSpec, CreatedVia
from aiagent.modules.agents.infrastructure.sql_agent_repository import SqlAgentRepository
from aiagent.modules.channels.domain import ShareLink, hash_secret
from aiagent.modules.channels.domain.telegram import TelegramBot
from aiagent.modules.channels.infrastructure.adapters import SqlChannelRepository
from aiagent.modules.channels.infrastructure.telegram import SqlTelegramBotRepository
from aiagent.modules.conversations.domain import RunStatus
from aiagent.modules.conversations.infrastructure.sql_conversation_repository import (
    SqlConversationRepository,
)
from aiagent.modules.ops.domain import OpsFilter
from aiagent.modules.ops.infrastructure import SqlOpsQuery
from aiagent.shared_kernel.clock import utcnow
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


async def _agent(db: Database) -> Agent:
    spec = AgentSpec.model_validate(
        {"name": f"P5 {uuid4().hex[:6]}", "model": {"provider": "fake", "model": "fake-echo"}}
    )
    agent, _ = await SqlAgentRepository(db).create("default", "it-user", spec, CreatedVia.API, "")
    return agent


async def test_feedback_and_ops_read_model(db: Database) -> None:
    agent = await _agent(db)
    conv = SqlConversationRepository(db)
    ops = SqlOpsQuery(db)
    users = ["it-user", f"share:{uuid4()}:visitor-aaaaaaaaaaaaaaaa", f"telegram:{uuid4()}:42"]
    threads = []
    for user in users:
        t = await conv.create_thread("default", user, agent.id, 1, False, f"t {user[:5]}")
        run = await conv.start_run("default", t.id, agent.id, 1, user, "fake-echo")
        await conv.append_message(
            "default", t.id, run.id, "user", [{"type": "text", "text": "hỏi"}]
        )
        answer = await conv.append_message(
            "default",
            t.id,
            run.id,
            "assistant",
            [
                {"type": "thinking", "thinking": "x", "signature": "s"},
                {"type": "text", "text": "đáp"},
            ],
        )
        await conv.finish_run(
            "default",
            run.id,
            RunStatus.COMPLETED,
            Usage(input_tokens=10, output_tokens=5),
            0.01,
            None,
        )
        threads.append((t, answer))

    t0, a0 = threads[0]
    await conv.upsert_feedback("default", t0.id, a0.id, agent.id, "it-user", 1, "")
    fb = await conv.upsert_feedback("default", t0.id, a0.id, agent.id, "it-user", -1, "sai")
    assert fb.rating == -1  # upsert theo (message, user)
    assert [f.rating for f in await conv.feedback_for_thread("default", t0.id)] == [-1]

    f = OpsFilter(agent_id=agent.id)
    rows = await ops.threads("default", f, 50, None)
    assert sorted(r.channel for r in rows) == ["app", "share", "telegram"]
    app_row = next(r for r in rows if r.channel == "app")
    assert app_row.message_count == 2 and app_row.thumbs_down == 1
    assert abs(app_row.cost_usd - 0.01) < 1e-9
    only_tg = await ops.threads(
        "default", OpsFilter(agent_id=agent.id, channel="telegram"), 50, None
    )
    assert [r.channel for r in only_tg] == ["telegram"]
    bad = await ops.threads("default", OpsFilter(agent_id=agent.id, rating=-1), 50, None)
    assert [r.id for r in bad] == [t0.id]

    items = await ops.feedback("default", f, 10)
    assert items[0].comment == "sai" and items[0].answer[-1]["text"] == "đáp"

    now = utcnow()
    usage = await ops.usage(
        "default",
        OpsFilter(
            agent_id=agent.id, since=now - timedelta(days=1), until=now + timedelta(minutes=1)
        ),
    )
    assert {u.channel for u in usage} == {"app", "share", "telegram"}
    assert sum(u.runs for u in usage) == 3 and sum(u.input_tokens for u in usage) == 30

    # RLS: tenant khác không thấy gì
    assert await ops.threads("other-tenant", f, 50, None) == []
    assert (
        await ops.usage(
            "other-tenant",
            OpsFilter(since=now - timedelta(days=1), until=now + timedelta(minutes=1)),
        )
        == []
    )


async def test_share_link_and_telegram_bot_repositories(db: Database) -> None:
    agent = await _agent(db)
    channels = SqlChannelRepository(db)
    now = utcnow()
    link = ShareLink(
        id=uuid4(),
        tenant_id="default",
        agent_id=agent.id,
        name="web",
        prefix="abcd",
        token_hash=hash_secret(f"tok-{uuid4()}"),
        enabled=True,
        allowed_origins=["https://aries.vn"],
        rate_limit_per_min=10,
        daily_message_limit=100,
        created_by="it-user",
        created_at=now,
        updated_at=now,
        last_used_at=None,
    )
    created = await channels.create_share(link)
    assert created.allowed_origins == ["https://aries.vn"]
    found = await channels.find_share("default", link.token_hash)
    assert found is not None
    # Lần tìm trước đã ghi last_used_at
    again = await channels.find_share("default", link.token_hash)
    assert again is not None and again.last_used_at is not None
    assert await channels.find_share("other-tenant", link.token_hash) is None

    repo = SqlTelegramBotRepository(db)
    bot = TelegramBot(
        id=uuid4(),
        tenant_id="default",
        agent_id=agent.id,
        name="Hỗ trợ",
        bot_user_id=int(uuid4().int % 10**12),
        bot_username="aries_it_bot",
        mode="polling",
        webhook_secret_hash=hash_secret("s"),
        enabled=True,
        allowed_chat_ids=[42, 7],
        rate_limit_per_min=20,
        update_offset=0,
        created_by="it-user",
        created_at=now,
        last_used_at=None,
    )
    saved = await repo.create(bot)
    assert saved.allowed_chat_ids == [42, 7] and saved.update_offset == 0
    await repo.set_offset("default", bot.id, 15)
    await repo.set_offset("default", bot.id, 3)  # không lùi offset
    await repo.touch("default", bot.id)
    got = await repo.get("default", bot.id)
    assert got is not None and got.update_offset == 15 and got.last_used_at is not None

    edited = await repo.save(replace(got, enabled=False, allowed_chat_ids=[], name="Mới"))
    assert (edited.enabled, edited.allowed_chat_ids, edited.name) == (False, [], "Mới")
    assert [b.id for b in await repo.list_for_agent("default", agent.id)] == [bot.id]
    assert await repo.get("other-tenant", bot.id) is None
    await repo.delete("default", bot.id)
    assert await repo.get("default", bot.id) is None
