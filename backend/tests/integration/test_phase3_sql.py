"""Phase 3a trên hạ tầng thật: approvals + connections (Postgres, RLS), OpenBao, connector Postgres."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from aiagent.adapters.db import Database
from aiagent.modules.agents.domain import AgentSpec, CreatedVia
from aiagent.modules.agents.infrastructure.sql_agent_repository import SqlAgentRepository
from aiagent.modules.connections.domain import Connection, ConnectionScope, ConnectionStatus
from aiagent.modules.connections.infrastructure.openbao_vault import OpenBaoVault
from aiagent.modules.connections.infrastructure.sql_connection_repository import (
    SqlConnectionRepository,
)
from aiagent.modules.conversations.domain import ApprovalStatus, DelegationStatus, RunStatus
from aiagent.modules.conversations.infrastructure.sql_conversation_repository import (
    SqlConversationRepository,
)
from aiagent.plugin_sdk import ConnectionContext, ToolContext
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import ConflictError
from aiagent.shared_kernel.usage import Usage
from aiagent_plugins.connector_postgres import PostgresConnector

pytestmark = pytest.mark.integration
DB_URL = os.environ.get("AIAGENT_TEST_DATABASE_URL")


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    if not DB_URL:
        pytest.skip("Chưa đặt AIAGENT_TEST_DATABASE_URL")
    database = Database(DB_URL, pool_size=2)
    yield database
    await database.dispose()


async def test_approvals_roundtrip_and_awaiting_status(db: Database) -> None:
    agents = SqlAgentRepository(db)
    conv = SqlConversationRepository(db)
    spec = AgentSpec.model_validate(
        {"name": "HITL", "model": {"provider": "fake", "model": "fake-echo"}}
    )
    agent, _ = await agents.create("default", "it-user", spec, CreatedVia.API, "")
    thread = await conv.create_thread("default", "it-user", agent.id, 1, False, "t")
    run = await conv.start_run("default", thread.id, agent.id, 1, "it-user", "fake-echo")
    created = await conv.create_approvals(
        "default",
        thread.id,
        run.id,
        agent.id,
        "it-user",
        [
            {
                "tool_use_id": "toolu_1",
                "tool": "db.execute",
                "risk": "destructive",
                "reason": "x",
                "arguments": {"sql": "delete from t where id=1"},
                "approver_roles": ["admin", "owner"],
                "connection_id": None,
            }
        ],
    )
    await conv.finish_run("default", run.id, RunStatus.AWAITING_APPROVAL, Usage(), 0.0, None)
    a = created[0]
    assert a.status is ApprovalStatus.PENDING and a.approver_roles == ["admin", "owner"]
    assert a.arguments == {"sql": "delete from t where id=1"}

    pending = await conv.list_approvals(
        "default",
        status=ApprovalStatus.PENDING,
        requester_id="it-user",
        thread_id=thread.id,
        limit=10,
    )
    assert [x.id for x in pending] == [a.id]
    decided = await conv.decide_approval("default", a.id, ApprovalStatus.DENIED, "admin-1", "no")
    assert decided is not None and decided.decided_at is not None
    assert await conv.decide_approval("default", a.id, ApprovalStatus.APPROVED, "x", None) is None
    found = await conv.approvals_for_tool_uses("default", thread.id, ["toolu_1"])
    assert found[0].status is ApprovalStatus.DENIED
    # RLS: tenant khác không thấy
    assert await conv.get_approval("other-tenant", a.id) is None


async def test_connection_repository_soft_delete_and_unique_name(db: Database) -> None:
    repo = SqlConnectionRepository(db)
    now = utcnow()
    name = f"IT conn {uuid4().hex[:6]}"
    c = Connection(
        id=uuid4(),
        tenant_id="default",
        name=name,
        connector_id="postgres",
        scope=ConnectionScope.PERSONAL,
        owner_id="it-user",
        config={"host": "h"},
        secret_keys=["password"],
        status=ConnectionStatus.ACTIVE,
        last_test_ok=None,
        last_test_note=None,
        last_test_at=None,
        created_at=now,
        updated_at=now,
    )
    created = await repo.create(c)
    assert created.config == {"host": "h"} and created.secret_keys == ["password"]
    with pytest.raises(ConflictError):
        await repo.create(replace(c, id=uuid4()))
    await repo.save(replace(created, status=ConnectionStatus.DELETED))
    assert all(x.id != c.id for x in await repo.list_all("default"))
    again = await repo.create(replace(c, id=uuid4()))  # tên dùng lại được sau khi xóa mềm
    assert again.name == name
    assert await repo.get("other-tenant", again.id) is None


async def test_openbao_vault_roundtrip() -> None:
    token = os.environ.get("AIAGENT_OPENBAO_TOKEN")
    addr = os.environ.get("AIAGENT_OPENBAO_ADDR")
    if not token or not addr:
        pytest.skip("Chưa cấu hình OpenBao")
    async with httpx.AsyncClient() as http:
        vault = OpenBaoVault(addr, token, http)
        path = f"aries/it/connections/{uuid4()}"
        await vault.put(path, {"password": "s3cret"})
        assert await vault.get(path) == {"password": "s3cret"}
        await vault.delete(path)
        assert await vault.get(path) == {}


async def test_postgres_connector_against_demo_db() -> None:
    password = os.environ.get("AIAGENT_TEST_DEMO_PASSWORD")
    if not password:
        pytest.skip("Chưa đặt AIAGENT_TEST_DEMO_PASSWORD (chạy demo-seed trước)")
    conn = ConnectionContext(
        id="c",
        name="demo",
        connector_id="postgres",
        config={
            "host": "postgres",
            "database": "aries_demo",
            "user": "aries_reader",
            "sslmode": "disable",
            "allowed_schemas": ["sales"],
            "max_rows": 3,
        },
        secrets={"password": password},
    )
    ctx = ToolContext(tenant_id="t", user_id="u", agent_id="a", run_id="r", connection=conn)
    plugin = PostgresConnector()

    health = await plugin.test_connection(conn)
    assert health.healthy and "PostgreSQL" in health.detail and "quyền ghi" not in health.detail

    tables = await plugin.call_tool("db.list_tables", {}, ctx)
    assert "sales.orders" in tables.content
    desc = await plugin.call_tool("db.describe_table", {"table": "sales.orders"}, ctx)
    assert "quantity: integer NOT NULL" in desc.content
    rows = await plugin.call_tool(
        "db.query", {"sql": "select id, name from customers order by id"}, ctx
    )
    assert not rows.is_error and '"truncated": true' in rows.content
    assert "Công ty An Phát" in rows.content

    blocked = await plugin.call_tool("db.query", {"sql": "select * from pg_authid"}, ctx)
    assert blocked.is_error
    # Role chỉ-đọc + transaction READ ONLY: kể cả khi bật allow_write vẫn không ghi được.
    writer = replace(conn, config={**conn.config, "allow_write": True})
    wctx = replace(ctx, connection=writer)
    denied = await plugin.call_tool("db.execute", {"sql": "delete from orders where id = 1"}, wctx)
    assert denied.is_error and "PostgreSQL" in denied.content


async def test_delegations_roundtrip(db: Database) -> None:
    agents = SqlAgentRepository(db)
    conv = SqlConversationRepository(db)
    spec = AgentSpec.model_validate(
        {"name": "Master IT", "graph_type": "supervisor",
         "model": {"provider": "fake", "model": "fake-echo"}}
    )
    agent, _ = await agents.create("default", "it-user", spec, CreatedVia.API, "")
    thread = await conv.create_thread("default", "it-user", agent.id, 1, False, "t")
    run = await conv.start_run("default", thread.id, agent.id, 1, "it-user", "fake-echo")
    d = await conv.create_delegation("default", thread.id, run.id, "toolu_m1", agent.id, "việc")
    assert d.status is DelegationStatus.RUNNING
    await conv.update_delegation(
        "default", d.id, status=DelegationStatus.COMPLETED, child_thread_id=thread.id,
        child_run_id=run.id, result="xong", tainted=True,
    )
    found = await conv.delegations_for_tool_uses("default", thread.id, ["toolu_m1"])
    assert found[0].result == "xong" and found[0].tainted
    assert await conv.delegations_for_tool_uses("other-tenant", thread.id, ["toolu_m1"]) == []
