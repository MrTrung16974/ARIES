from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from asgi_lifespan import LifespanManager

from aiagent.bootstrap.container import (
    ConnectionAccessAdapter,
    ConnectionCatalogAdapter,
    Container,
    PluginCatalogProvider,
    PluginConnectorLookup,
    PluginSkillCatalog,
)
from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.bootstrap.settings import Settings
from aiagent.main import create_app
from aiagent.modules.agents.application import AgentService
from aiagent.modules.audit.application import AuditService
from aiagent.modules.builder.application import ArchitectService, CommandService
from aiagent.modules.builder.infrastructure.heuristic_brain import HeuristicArchitectBrain
from aiagent.modules.connections.application import ConnectionService
from aiagent.modules.conversations.application import ConversationService
from aiagent.modules.identity.application import IdentityService
from aiagent.modules.identity.infrastructure.jwks_verifier import JwksTokenVerifier
from aiagent.modules.runtime.application import (
    ApprovalService,
    ModelRouter,
    RunAgentService,
    ToolGateway,
)
from aiagent.modules.runtime.infrastructure.fake_model import FakeChatModel
from aiagent.modules.runtime.infrastructure.react_graph import LangGraphReactLoop
from aiagent.shared_kernel.events import InProcessEventBus
from tests.conftest import AUDIENCE, ISSUER, JWKS_URL
from tests.fakes import (
    FakeDbConnector,
    FakeUsers,
    InMemoryAgentRepository,
    InMemoryAuditLog,
    InMemoryBuilderSessionRepository,
    InMemoryConnectionRepository,
    InMemoryConversationRepository,
    InMemoryVault,
    NotifySkill,
    source_with,
)


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def conv_repo() -> InMemoryConversationRepository:
    return InMemoryConversationRepository()


@pytest.fixture
def notify() -> NotifySkill:
    return NotifySkill()


@pytest.fixture
def fakedb() -> FakeDbConnector:
    return FakeDbConnector()


@pytest.fixture
def vault() -> InMemoryVault:
    return InMemoryVault()


@pytest.fixture
def ready() -> dict[str, bool]:
    return {"database": True, "redis": True}


@pytest.fixture
async def client(
    jwks_transport, audit_log, ready, conv_repo, notify, fakedb, vault
) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    transport, _ = jwks_transport
    settings = Settings(env="test", log_json=False, oidc_issuer=ISSUER, oidc_audience=AUDIENCE)

    def factory(s: Settings) -> Container:
        async def check_db() -> bool:
            return ready["database"]

        async def check_redis() -> bool:
            return ready["redis"]

        plugins = PluginRegistry(source=source_with(notify, fakedb))
        plugins.load_all()
        audit = AuditService(audit_log)
        conversations = ConversationService(conv_repo)
        router = ModelRouter({"fake": FakeChatModel()})
        connections = ConnectionService(
            InMemoryConnectionRepository(), vault, PluginConnectorLookup(plugins), audit
        )
        agents = AgentService(
            InMemoryAgentRepository(),
            PluginSkillCatalog(plugins),
            connections=ConnectionCatalogAdapter(connections, plugins),
        )
        gateway = ToolGateway(plugins, audit, access=ConnectionAccessAdapter(connections))
        runs = RunAgentService(agents, conversations, gateway, router, LangGraphReactLoop(), audit)
        architect = ArchitectService(
            InMemoryBuilderSessionRepository(),
            HeuristicArchitectBrain(),
            agents,
            runs,
            PluginCatalogProvider(plugins, router, connections),
            audit,
        )
        return Container(
            settings=s,
            token_verifier=JwksTokenVerifier(
                issuer=ISSUER,
                audience=AUDIENCE,
                jwks_url=JWKS_URL,
                http=httpx.AsyncClient(transport=transport),
            ),
            identity=IdentityService(FakeUsers()),
            audit=audit,
            plugins=plugins,
            event_bus=InProcessEventBus(),
            agents=agents,
            conversations=conversations,
            runs=runs,
            approvals=ApprovalService(conv_repo, audit),
            connections=connections,
            model_router=router,
            architect=architect,
            commands=CommandService(architect, agents, audit),
            health_checks={"database": check_db, "redis": check_redis},
        )

    app = create_app(settings, container_factory=factory)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_healthz_and_security_headers(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert len(r.headers["x-request-id"]) >= 8


async def test_readyz_reports_degraded(client: httpx.AsyncClient, ready: dict[str, bool]) -> None:
    assert (await client.get("/readyz")).status_code == 200
    ready["redis"] = False
    r = await client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"] == {"database": True, "redis": False}


async def test_me_requires_token(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["error"]["code"] == "unauthorized"


async def test_me_rejects_bad_token(
    client: httpx.AsyncClient, make_token: Callable[..., str]
) -> None:
    r = await client.get("/api/v1/me", headers=auth(make_token(aud="other")))
    assert r.status_code == 401


async def test_me_returns_profile(
    client: httpx.AsyncClient, make_token: Callable[..., str]
) -> None:
    r = await client.get("/api/v1/me", headers=auth(make_token(roles=["builder"])))
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "user-1"
    assert body["tenant_id"] == "default"
    assert body["roles"] == ["builder"]


async def test_role_denied_is_audited(
    client: httpx.AsyncClient, make_token: Callable[..., str], audit_log: InMemoryAuditLog
) -> None:
    r = await client.get("/api/v1/audit/events", headers=auth(make_token(roles=["user"])))
    assert r.status_code == 403
    assert len(audit_log.records) == 1
    event = audit_log.records[0].event
    assert event.action == "authz.denied"
    assert event.resource_id == "GET /api/v1/audit/events"


async def test_auditor_can_read_and_verify(
    client: httpx.AsyncClient, make_token: Callable[..., str]
) -> None:
    await client.get(
        "/api/v1/system/plugins", headers=auth(make_token(roles=["user"]))
    )  # tạo 1 bản ghi
    headers = auth(make_token(roles=["auditor"]))
    events = (await client.get("/api/v1/audit/events", headers=headers)).json()
    assert [e["action"] for e in events] == ["authz.denied"]
    verify = (await client.get("/api/v1/audit/verify", headers=headers)).json()
    assert verify == {"intact": True, "records_checked": 1, "broken_at_seq": None}


async def test_admin_lists_plugins(
    client: httpx.AsyncClient, make_token: Callable[..., str]
) -> None:
    r = await client.get("/api/v1/system/plugins", headers=auth(make_token(roles=["admin"])))
    assert r.status_code == 200
    assert any(p["id"] == "echo" and p["status"] == "active" for p in r.json())


async def test_owner_passes_every_role_check(
    client: httpx.AsyncClient, make_token: Callable[..., str]
) -> None:
    r = await client.get("/api/v1/audit/verify", headers=auth(make_token(roles=["owner"])))
    assert r.status_code == 200


# ---------------------------------------------------------------- agents + chat (Phase 1)
import json  # noqa: E402

SPEC = {
    "name": "Trợ lý thử",
    "model": {"provider": "fake", "model": "fake-echo"},
    "skills": [{"skill_id": "echo"}, {"skill_id": "clock"}],
    "rules": ["Trả lời ngắn gọn"],
}


def parse_sse(body: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
    events = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines() if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def create_published(client: httpx.AsyncClient, token: str) -> str:
    r = await client.post("/api/v1/agents", json={"spec": SPEC}, headers=auth(token))
    assert r.status_code == 201, r.text
    agent_id = r.json()["id"]
    r = await client.post(
        f"/api/v1/agents/{agent_id}/publish", json={"version": 1}, headers=auth(token)
    )
    assert r.status_code == 200
    return str(agent_id)


async def test_user_cannot_create_agent(client: httpx.AsyncClient, make_token) -> None:  # type: ignore[no-untyped-def]
    r = await client.post("/api/v1/agents", json={"spec": SPEC}, headers=auth(make_token()))
    assert r.status_code == 403


async def test_invalid_spec_rejected(client: httpx.AsyncClient, make_token) -> None:  # type: ignore[no-untyped-def]
    builder = auth(make_token(roles=["builder"]))
    bad = {"spec": {**SPEC, "skills": [{"skill_id": "ghost"}]}}
    r = await client.post("/api/v1/agents", json=bad, headers=builder)
    assert r.status_code == 422
    assert "ghost" in r.json()["error"]["message"]
    r = await client.post(
        "/api/v1/agents", json={"spec": {"name": "x", "temperature": 1}}, headers=builder
    )
    assert r.status_code == 422


async def test_catalog_and_schema(client: httpx.AsyncClient, make_token) -> None:  # type: ignore[no-untyped-def]
    builder = auth(make_token(roles=["builder"]))
    skills = (await client.get("/api/v1/catalog/skills", headers=builder)).json()
    assert {"echo", "clock", "notify"} <= {s["id"] for s in skills}
    models = (await client.get("/api/v1/catalog/models", headers=builder)).json()
    assert {"provider": "fake", "model": "fake-echo", "available": True} in models
    assert any(m["provider"] == "anthropic" and not m["available"] for m in models)
    schema = (await client.get("/api/v1/agents/schema", headers=builder)).json()
    assert "skills" in schema["properties"]


async def test_chat_end_to_end_with_tool(
    client: httpx.AsyncClient,
    make_token,
    conv_repo,
    audit_log,  # type: ignore[no-untyped-def]
) -> None:
    agent_id = await create_published(client, make_token(sub="b1", roles=["builder"]))
    user = auth(make_token(sub="u1"))

    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=user,
        json={"message": '/tool echo__say {"text": "xin chào"}'},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    names = [e[0] for e in events]
    assert names[0] == "run_started" and names[-1] == "done"
    assert "tool_start" in names and "tool_end" in names
    assert events[-1][1]["status"] == "completed"
    tool_end = next(d for n, d in events if n == "tool_end")
    assert tool_end["name"] == "echo.say" and tool_end["preview"] == "xin chào"
    assert "message" not in names  # tin nhắn đầy đủ chỉ để lưu, không gửi client

    thread_id = events[0][1]["thread_id"]
    msgs = (await client.get(f"/api/v1/threads/{thread_id}/messages", headers=user)).json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert {"agent.run", "tool.invoked"} <= set(audit_log.actions())

    # Tiếp tục cùng hội thoại
    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=user,
        json={"message": "cảm ơn", "thread_id": thread_id},
    )
    tokens = "".join(d["text"] for n, d in parse_sse(r.text) if n == "token")
    assert tokens == "Bạn vừa nói: cảm ơn"

    # Người khác không đọc được hội thoại
    other = auth(make_token(sub="u2"))
    assert (
        await client.get(f"/api/v1/threads/{thread_id}/messages", headers=other)
    ).status_code == 404
    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=other,
        json={"message": "hi", "thread_id": thread_id},
    )
    assert r.status_code == 404


async def test_new_version_requires_new_thread_and_draft_is_editor_only(
    client: httpx.AsyncClient,
    make_token,  # type: ignore[no-untyped-def]
) -> None:
    builder_token = make_token(sub="b1", roles=["builder"])
    agent_id = await create_published(client, builder_token)
    user = auth(make_token(sub="u1"))
    r = await client.post(f"/api/v1/agents/{agent_id}/chat", headers=user, json={"message": "a"})
    thread_id = parse_sse(r.text)[0][1]["thread_id"]

    r = await client.put(
        f"/api/v1/agents/{agent_id}",
        headers=auth(builder_token),
        json={"spec": {**SPEC, "name": "v2"}, "expected_version": 1},
    )
    assert r.status_code == 200 and r.json()["version"] == 2
    await client.post(
        f"/api/v1/agents/{agent_id}/publish", json={"version": 2}, headers=auth(builder_token)
    )

    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=user,
        json={"message": "b", "thread_id": thread_id},
    )
    assert r.status_code == 409

    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat", headers=user, json={"message": "b", "draft": True}
    )
    assert r.status_code == 403
    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=auth(builder_token),
        json={"message": "b", "draft": True},
    )
    assert parse_sse(r.text)[0][1]["draft"] is True


async def test_chat_unpublished_agent_and_missing_provider(
    client: httpx.AsyncClient,
    make_token,  # type: ignore[no-untyped-def]
) -> None:
    builder = auth(make_token(sub="b1", roles=["builder"]))
    r = await client.post("/api/v1/agents", json={"spec": SPEC}, headers=builder)
    agent_id = r.json()["id"]
    r = await client.post(f"/api/v1/agents/{agent_id}/chat", headers=builder, json={"message": "x"})
    assert r.status_code == 422  # chưa publish

    claude = {**SPEC, "model": {"provider": "anthropic", "model": "claude-opus-5-5"}}
    r = await client.post("/api/v1/agents", json={"spec": claude}, headers=builder)
    r = await client.post(
        f"/api/v1/agents/{r.json()['id']}/chat",
        headers=builder,
        json={"message": "x", "draft": True},
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "llm_unavailable"


async def test_builder_api_sse_and_commands(client: httpx.AsyncClient, make_token) -> None:  # type: ignore[no-untyped-def]
    builder = auth(make_token(sub="b1", roles=["builder"]))
    assert (
        await client.post("/api/v1/builder/sessions", json={}, headers=auth(make_token()))
    ).status_code == 403

    session = (await client.post("/api/v1/builder/sessions", json={}, headers=builder)).json()
    r = await client.post(
        f"/api/v1/builder/sessions/{session['id']}/messages",
        headers=builder,
        json={"message": "Tạo trợ lý nội bộ báo giờ"},
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    names = [n for n, _ in parse_sse(r.text)]
    assert {"spec_draft", "eval_result", "done"} <= set(names)

    state = (await client.get(f"/api/v1/builder/sessions/{session['id']}", headers=builder)).json()
    assert state["status"] == "ready" and state["spec"]["skills"][0]["skill_id"] == "clock"
    assert [m["role"] for m in state["messages"]] == ["user", "architect"]

    other = auth(make_token(sub="b2", roles=["builder"]))
    assert (
        await client.get(f"/api/v1/builder/sessions/{session['id']}", headers=other)
    ).status_code == 404

    r = await client.post(
        f"/api/v1/builder/sessions/{session['id']}/confirm",
        headers=builder,
        json={"action": "publish", "override_eval": True},
    )
    assert r.status_code == 200 and r.json()["version"] == 1

    r = await client.post("/api/v1/commands", headers=builder, json={"text": "/agent list"})
    assert r.json()["kind"] == "result"
    r = await client.post("/api/v1/commands", headers=builder, json={"text": "/nope"})
    assert r.json()["kind"] == "error"
    bad = await client.post(
        f"/api/v1/builder/sessions/{session['id']}/messages", headers=builder, json={}
    )
    assert bad.status_code == 422


# ---------------------------------------------------------------- HITL (Phase 3a)
async def test_hitl_approve_then_resume(
    client: httpx.AsyncClient,
    make_token,
    notify,
    audit_log,  # type: ignore[no-untyped-def]
) -> None:
    builder = make_token(sub="b1", roles=["builder"])
    spec = {**SPEC, "skills": [{"skill_id": "notify"}]}
    r = await client.post("/api/v1/agents", json={"spec": spec}, headers=auth(builder))
    agent_id = r.json()["id"]
    await client.post(
        f"/api/v1/agents/{agent_id}/publish", json={"version": 1}, headers=auth(builder)
    )
    user = auth(make_token(sub="u1"))

    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=user,
        json={"message": '/tool notify__send {"to": "kế toán"}'},
    )
    events = parse_sse(r.text)
    names = [e[0] for e in events]
    assert "approval_required" in names and "tool_end" not in names
    assert events[-1][1]["status"] == "awaiting_approval"
    req = next(d for n, d in events if n == "approval_required")["requests"][0]
    assert req["tool"] == "notify.send" and req["arguments"] == {"to": "kế toán"}
    assert notify.sent == []
    thread_id = events[0][1]["thread_id"]

    # Chưa quyết định → không resume được
    r = await client.post(f"/api/v1/threads/{thread_id}/resume", headers=user)
    assert r.status_code == 409

    # Người khác không thấy / không duyệt được
    other = auth(make_token(sub="u2"))
    assert (await client.get("/api/v1/approvals", headers=other)).json() == []
    r = await client.post(
        f"/api/v1/approvals/{req['approval_id']}/decision", headers=other, json={"approve": True}
    )
    assert r.status_code == 404

    pending = (await client.get("/api/v1/approvals?status=pending", headers=user)).json()
    assert [a["id"] for a in pending] == [req["approval_id"]] and pending[0]["can_decide"]
    r = await client.post(
        f"/api/v1/approvals/{req['approval_id']}/decision",
        headers=user,
        json={"approve": True, "note": "ok"},
    )
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = await client.post(
        f"/api/v1/approvals/{req['approval_id']}/decision", headers=user, json={"approve": False}
    )
    assert r.status_code == 409  # đã quyết định

    r = await client.post(f"/api/v1/threads/{thread_id}/resume", headers=user)
    assert r.status_code == 200
    events = parse_sse(r.text)
    assert events[-1][1]["status"] == "completed"
    tool_end = next(d for n, d in events if n == "tool_end")
    assert tool_end["name"] == "notify.send" and not tool_end["is_error"]
    assert notify.sent == [{"tool": "notify.send", "to": "kế toán"}]
    tokens = "".join(d["text"] for n, d in events if n == "token")
    assert "đã gửi tới kế toán" in tokens
    invoked = [r for r in audit_log.records if r.event.action == "tool.invoked"]
    assert invoked[-1].event.details["approved_by"] == "u1"
    assert {"approval.requested", "approval.decided"} <= set(audit_log.actions())


async def test_hitl_deny_and_destructive_needs_admin(
    client: httpx.AsyncClient,
    make_token,
    notify,  # type: ignore[no-untyped-def]
) -> None:
    builder = make_token(sub="b1", roles=["builder"])
    spec = {**SPEC, "skills": [{"skill_id": "notify"}]}
    r = await client.post("/api/v1/agents", json={"spec": spec}, headers=auth(builder))
    agent_id = r.json()["id"]
    await client.post(
        f"/api/v1/agents/{agent_id}/publish", json={"version": 1}, headers=auth(builder)
    )
    user = auth(make_token(sub="u1"))

    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat", headers=user, json={"message": "/tool notify__drop {}"}
    )
    events = parse_sse(r.text)
    req = next(d for n, d in events if n == "approval_required")["requests"][0]
    assert req["approver_roles"] == ["admin", "owner"]
    thread_id = events[0][1]["thread_id"]

    # Người yêu cầu không tự duyệt thao tác phá hủy
    r = await client.post(
        f"/api/v1/approvals/{req['approval_id']}/decision", headers=user, json={"approve": True}
    )
    assert r.status_code == 403
    admin = auth(make_token(sub="a1", roles=["admin"]))
    inbox = (await client.get("/api/v1/approvals?status=pending", headers=admin)).json()
    assert inbox[0]["can_decide"]
    r = await client.post(
        f"/api/v1/approvals/{req['approval_id']}/decision",
        headers=admin,
        json={"approve": False, "note": "không được xóa"},
    )
    assert r.json()["status"] == "denied"

    r = await client.post(f"/api/v1/threads/{thread_id}/resume", headers=user)
    events = parse_sse(r.text)
    tool_end = next(d for n, d in events if n == "tool_end")
    assert tool_end["is_error"] and tool_end["denied"]
    assert notify.sent == []
    tokens = "".join(d["text"] for n, d in events if n == "token")
    assert "TỪ CHỐI" in tokens and "không được xóa" in tokens

    # Tin nhắn mới khi đang chờ duyệt → yêu cầu cũ hết hiệu lực
    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=user,
        json={"message": "/tool notify__drop {}", "thread_id": thread_id},
    )
    req2 = next(d for n, d in parse_sse(r.text) if n == "approval_required")["requests"][0]
    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=user,
        json={"message": "thôi", "thread_id": thread_id},
    )
    assert r.status_code == 200
    expired = (await client.get(f"/api/v1/approvals?thread_id={thread_id}", headers=admin)).json()
    assert next(a for a in expired if a["id"] == req2["approval_id"])["status"] == "expired"


# ---------------------------------------------------------------- connections (Phase 3a)
async def test_connection_lifecycle_and_permissions(
    client: httpx.AsyncClient,
    make_token,
    vault,
    audit_log,  # type: ignore[no-untyped-def]
) -> None:
    builder = auth(make_token(sub="b1", roles=["builder"]))
    user = auth(make_token(sub="u1"))
    admin = auth(make_token(sub="a1", roles=["admin"]))

    connectors = (await client.get("/api/v1/connectors", headers=user)).json()
    fake = next(x for x in connectors if x["id"] == "fakedb")
    assert fake["secrets"] == ["password"] and len(fake["tools"]) == 2

    body = {
        "name": "Kho dữ liệu",
        "connector_id": "fakedb",
        "config": {"host": "db1"},
        "secrets": {"password": "pw"},
    }
    assert (await client.post("/api/v1/connections", headers=user, json=body)).status_code == 403
    r = await client.post(
        "/api/v1/connections", headers=builder, json={**body, "scope": "workspace"}
    )
    assert r.status_code == 403
    for bad in (
        {**body, "secrets": {}},
        {**body, "secrets": {"password": "pw", "token": "x"}},
        {**body, "config": {"host": "db1", "password": "pw"}},
        {**body, "config": {"port": "x"}},
        {**body, "connector_id": "notify"},
    ):
        r = await client.post("/api/v1/connections", headers=builder, json=bad)
        assert r.status_code == 422, (bad, r.text)

    r = await client.post("/api/v1/connections", headers=builder, json=body)
    assert r.status_code == 201, r.text
    conn = r.json()
    cid = conn["id"]
    assert conn["secret_keys"] == ["password"] and "pw" not in r.text
    assert conn["can_manage"] and conn["can_use"]
    assert list(vault.data.values()) == [{"password": "pw"}]
    dup = await client.post("/api/v1/connections", headers=builder, json=body)
    assert dup.status_code == 409

    # Người khác không thấy kết nối personal; Admin thấy và quản lý được
    assert (await client.get("/api/v1/connections", headers=user)).json() == []
    assert (await client.get(f"/api/v1/connections/{cid}", headers=user)).status_code == 404
    seen = (await client.get("/api/v1/connections", headers=admin)).json()
    assert seen[0]["can_manage"] and not seen[0]["can_use"]

    tested = (await client.post(f"/api/v1/connections/{cid}/test", headers=builder)).json()
    assert tested["last_test_ok"] is True
    r = await client.patch(
        f"/api/v1/connections/{cid}",
        headers=builder,
        json={"secrets": {"password": "sai"}, "name": "Kho 2"},
    )
    assert r.json()["name"] == "Kho 2"
    tested = (await client.post(f"/api/v1/connections/{cid}/test", headers=builder)).json()
    assert tested["last_test_ok"] is False and tested["last_test_note"] == "Sai mật khẩu"
    # Bỏ trống secret = giữ nguyên
    await client.patch(
        f"/api/v1/connections/{cid}", headers=builder, json={"secrets": {"password": ""}}
    )
    assert list(vault.data.values()) == [{"password": "sai"}]

    r = await client.delete(f"/api/v1/connections/{cid}", headers=builder)
    assert r.status_code == 204 and vault.data == {}
    assert (await client.get("/api/v1/connections", headers=builder)).json() == []
    actions = audit_log.actions()
    for a in (
        "connection.created",
        "connection.updated",
        "connection.tested",
        "connection.deleted",
    ):
        assert a in actions
    assert "pw" not in json.dumps([rec.event.details for rec in audit_log.records])


async def test_agent_uses_connection_with_taint_and_approval(
    client: httpx.AsyncClient,
    make_token,
    fakedb,  # type: ignore[no-untyped-def]
) -> None:
    b1 = make_token(sub="b1", roles=["builder"])
    r = await client.post(
        "/api/v1/connections",
        headers=auth(b1),
        json={
            "name": "DB bán hàng",
            "connector_id": "fakedb",
            "config": {"host": "sales"},
            "secrets": {"password": "pw"},
        },
    )
    conn_id = r.json()["id"]
    spec = {**SPEC, "skills": [], "connections": [{"connection_id": conn_id}]}
    r = await client.post("/api/v1/agents", json={"spec": spec}, headers=auth(b1))
    agent_id = r.json()["id"]
    await client.post(f"/api/v1/agents/{agent_id}/publish", json={"version": 1}, headers=auth(b1))

    # Chính chủ chạy: tool đọc chạy luôn, output bị bọc nhãn không tin cậy
    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=auth(b1),
        json={"message": "/tool fakedb__query {}"},
    )
    events = parse_sse(r.text)
    assert events[-1][1]["status"] == "completed"
    tool_end = next(d for n, d in events if n == "tool_end")
    assert tool_end["preview"].startswith("⟦DỮ LIỆU BÊN NGOÀI")
    assert fakedb.seen_secrets == ["pw"]
    thread_id = events[0][1]["thread_id"]

    # Sau khi đọc dữ liệu ngoài, thao tác ghi cần duyệt (taint kế thừa qua lịch sử)
    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=auth(b1),
        json={"message": "/tool fakedb__exec {}", "thread_id": thread_id},
    )
    events = parse_sse(r.text)
    assert events[-1][1]["status"] == "awaiting_approval"
    req = next(d for n, d in events if n == "approval_required")["requests"][0]
    assert req["connection_id"] == conn_id and "bên ngoài" in req["reason"]

    # Người khác không có quyền dùng connection personal → tool bị ẩn
    r = await client.post(
        f"/api/v1/agents/{agent_id}/chat",
        headers=auth(make_token(sub="u9")),
        json={"message": "/tool fakedb__query {}"},
    )
    events = parse_sse(r.text)
    assert events[0][1]["hidden_tools"] == [f"connection:{conn_id}"]
    assert "tool_start" not in [n for n, _ in events]
    assert fakedb.seen_secrets == ["pw"]


async def _make_connection(client: httpx.AsyncClient, token: str, name: str) -> str:
    r = await client.post(
        "/api/v1/connections",
        headers=auth(token),
        json={
            "name": name,
            "connector_id": "fakedb",
            "config": {"host": "sales"},
            "secrets": {"password": "pw"},
        },
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_agent_cannot_attach_foreign_connection(
    client: httpx.AsyncClient,
    make_token,  # type: ignore[no-untyped-def]
) -> None:
    b1, b2 = make_token(sub="b1", roles=["builder"]), make_token(sub="b2", roles=["builder"])
    mine = await _make_connection(client, b1, "Của b1")
    spec = {**SPEC, "connections": [{"connection_id": mine}]}
    r = await client.post("/api/v1/agents", json={"spec": spec}, headers=auth(b2))
    assert r.status_code == 422 and "không được dùng" in r.text
    bad_tool = {**SPEC, "connections": [{"connection_id": mine, "allowed_tools": ["x.y"]}]}
    r = await client.post("/api/v1/agents", json={"spec": bad_tool}, headers=auth(b1))
    assert r.status_code == 422 and "x.y" in r.text

    # b1 tạo được; Admin sửa agent (giữ nguyên connection cũ) vẫn lưu được
    r = await client.post("/api/v1/agents", json={"spec": spec}, headers=auth(b1))
    assert r.status_code == 201
    agent_id = r.json()["id"]
    admin = auth(make_token(sub="a1", roles=["admin"]))
    r = await client.put(
        f"/api/v1/agents/{agent_id}",
        headers=admin,
        json={"spec": {**spec, "rules": ["Ngắn gọn"]}, "expected_version": 1},
    )
    assert r.status_code == 200, r.text


async def test_architect_picks_connection_and_reports_permissions(
    client: httpx.AsyncClient,
    make_token,  # type: ignore[no-untyped-def]
) -> None:
    b1 = make_token(sub="b1", roles=["builder"])
    builder = auth(b1)

    # Chưa có kết nối → Architect báo thiếu
    session = (await client.post("/api/v1/builder/sessions", json={}, headers=builder)).json()
    r = await client.post(
        f"/api/v1/builder/sessions/{session['id']}/messages",
        headers=builder,
        json={"message": "Tạo trợ lý nội bộ tra cứu đơn hàng trong cơ sở dữ liệu"},
    )
    draft = next(d for n, d in parse_sse(r.text) if n == "spec_draft")
    assert draft["spec"]["connections"] == []
    assert any(m["kind"] == "connection" for m in draft["missing"])

    conn_id = await _make_connection(client, b1, "DB bán hàng")
    session = (await client.post("/api/v1/builder/sessions", json={}, headers=builder)).json()
    r = await client.post(
        f"/api/v1/builder/sessions/{session['id']}/messages",
        headers=builder,
        json={"message": "Tạo trợ lý nội bộ tra cứu đơn hàng trong cơ sở dữ liệu"},
    )
    draft = next(d for n, d in parse_sse(r.text) if n == "spec_draft")
    assert draft["spec"]["connections"] == [
        {"connection_id": conn_id, "allowed_tools": ["fakedb.query"]}
    ]
    perms = draft["permissions"]
    assert {"connection": "DB bán hàng", "tool": "fakedb.query"}.items() <= perms[0].items()
    r = await client.post(
        f"/api/v1/builder/sessions/{session['id']}/confirm",
        headers=builder,
        json={"action": "publish", "override_eval": True},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- Master agent (Phase 3c)
async def _publish(client: httpx.AsyncClient, token: str, spec: dict) -> str:  # type: ignore[type-arg]
    r = await client.post("/api/v1/agents", json={"spec": spec}, headers=auth(token))
    assert r.status_code == 201, r.text
    agent_id = str(r.json()["id"])
    r = await client.post(
        f"/api/v1/agents/{agent_id}/publish", json={"version": 1}, headers=auth(token)
    )
    assert r.status_code == 200, r.text
    return agent_id


async def test_master_delegates_to_member_agent(
    client: httpx.AsyncClient,
    make_token,
    conv_repo,
    audit_log,  # type: ignore[no-untyped-def]
) -> None:
    b1 = make_token(sub="b1", roles=["builder"])
    member = await _publish(
        client, b1, {**SPEC, "name": "Trợ lý thử", "when_to_use": "Khi cần lặp lại văn bản"}
    )
    secret = await _publish(
        client, b1, {**SPEC, "name": "Kho mật", "data_classification": "confidential"}
    )
    user = auth(make_token(sub="u1"))

    master = (await client.get("/api/v1/master", headers=user)).json()
    again = (await client.get("/api/v1/master", headers=user)).json()
    assert master["agent_id"] == again["agent_id"] and master["name"] == "ARIES Master"

    task = '/tool echo__say {\\"text\\": \\"xin chào\\"}'
    r = await client.post(
        f"/api/v1/agents/{master['agent_id']}/chat",
        headers=user,
        json={"message": '/tool delegate__tro_ly_thu {"task": "' + task + '"}'},
    )
    events = parse_sse(r.text)
    names = [n for n, _ in events]
    assert events[-1][1]["status"] == "completed", events
    start = next(d for n, d in events if n == "delegation_start")
    assert start["agent_id"] == member and start["agent_name"] == "Trợ lý thử"
    progress = [d for n, d in events if n == "delegation_progress"]
    assert [p["event"] for p in progress] == ["tool_start", "tool_end"]
    end = next(d for n, d in events if n == "delegation_end")
    assert end["status"] == "completed"
    assert names.index("delegation_start") < names.index("delegation_end")
    tokens = "".join(d["text"] for n, d in events if n == "token")
    assert "Kết quả từ agent “Trợ lý thử”" in tokens and "xin chào" in tokens

    # Run con là một hội thoại riêng của chính người dùng
    threads = (await client.get(f"/api/v1/threads?agent_id={member}", headers=user)).json()
    assert len(threads) == 1
    assert {"delegation.started", "delegation.finished"} <= set(audit_log.actions())

    # Agent mật hơn Master không được đưa vào nhóm
    r = await client.post(
        f"/api/v1/agents/{master['agent_id']}/chat",
        headers=user,
        json={"message": '/tool delegate__kho_mat {"task": "x"}'},
    )
    first = parse_sse(r.text)[0][1]
    assert f"agent:{secret}" in first["hidden_tools"]
    assert "delegation_start" not in [n for n, _ in parse_sse(r.text)]


async def test_master_nested_approval_and_resume(
    client: httpx.AsyncClient,
    make_token,
    notify,  # type: ignore[no-untyped-def]
) -> None:
    b1 = make_token(sub="b1", roles=["builder"])
    await _publish(client, b1, {**SPEC, "name": "Thông báo", "skills": [{"skill_id": "notify"}]})
    user = auth(make_token(sub="u1"))
    master = (await client.get("/api/v1/master", headers=user)).json()["agent_id"]

    task = '/tool notify__send {\\"to\\": \\"kế toán\\"}'
    r = await client.post(
        f"/api/v1/agents/{master}/chat",
        headers=user,
        json={"message": '/tool delegate__thong_bao {"task": "' + task + '"}'},
    )
    events = parse_sse(r.text)
    assert events[-1][1]["status"] == "awaiting_approval", events
    req = next(d for n, d in events if n == "approval_required")["requests"][0]
    assert req["delegation_id"] and req["approval_id"] and req["agent_name"] == "Thông báo"
    assert notify.sent == []
    thread = events[0][1]["thread_id"]

    state = (await client.get(f"/api/v1/threads/{thread}/pending", headers=user)).json()
    assert state["waiting"] and not state["can_resume"]
    assert [a["id"] for a in state["approvals"]] == [req["approval_id"]]
    assert (await client.post(f"/api/v1/threads/{thread}/resume", headers=user)).status_code == 409

    r = await client.post(
        f"/api/v1/approvals/{req['approval_id']}/decision", headers=user, json={"approve": True}
    )
    assert r.status_code == 200
    state = (await client.get(f"/api/v1/threads/{thread}/pending", headers=user)).json()
    assert state["can_resume"]

    r = await client.post(f"/api/v1/threads/{thread}/resume", headers=user)
    events = parse_sse(r.text)
    assert events[-1][1]["status"] == "completed", events
    assert notify.sent == [{"tool": "notify.send", "to": "kế toán"}]
    assert next(d for n, d in events if n == "delegation_start")["resumed"]
    tokens = "".join(d["text"] for n, d in events if n == "token")
    assert "đã gửi tới kế toán" in tokens
    state = (await client.get(f"/api/v1/threads/{thread}/pending", headers=user)).json()
    assert not state["waiting"]


async def test_supervisor_spec_rules(client: httpx.AsyncClient, make_token) -> None:  # type: ignore[no-untyped-def]
    b1 = make_token(sub="b1", roles=["builder"])
    bad = {**SPEC, "graph_type": "supervisor"}  # SPEC có skill → không hợp lệ
    r = await client.post("/api/v1/agents", json={"spec": bad}, headers=auth(b1))
    assert r.status_code == 422
    sup = await _publish(
        client, b1, {**SPEC, "name": "Điều phối", "skills": [], "graph_type": "supervisor"}
    )
    nested = {
        **SPEC,
        "name": "Điều phối 2",
        "skills": [],
        "graph_type": "supervisor",
        "team": {"mode": "fixed", "members": [{"agent_id": sup}]},
    }
    r = await client.post("/api/v1/agents", json={"spec": nested}, headers=auth(b1))
    assert r.status_code == 422 and "điều phối" in r.text
