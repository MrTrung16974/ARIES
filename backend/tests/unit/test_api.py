from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from asgi_lifespan import LifespanManager

from aiagent.bootstrap.container import Container, PluginSkillCatalog
from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.bootstrap.settings import Settings
from aiagent.main import create_app
from aiagent.modules.agents.application import AgentService
from aiagent.modules.audit.application import AuditService
from aiagent.modules.conversations.application import ConversationService
from aiagent.modules.identity.application import IdentityService
from aiagent.modules.identity.infrastructure.jwks_verifier import JwksTokenVerifier
from aiagent.modules.runtime.application import ModelRouter, RunAgentService, ToolGateway
from aiagent.modules.runtime.infrastructure.fake_model import FakeChatModel
from aiagent.modules.runtime.infrastructure.react_graph import LangGraphReactLoop
from aiagent.shared_kernel.events import InProcessEventBus
from tests.conftest import AUDIENCE, ISSUER, JWKS_URL
from tests.fakes import (
    FakeUsers,
    InMemoryAgentRepository,
    InMemoryAuditLog,
    InMemoryConversationRepository,
)


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def conv_repo() -> InMemoryConversationRepository:
    return InMemoryConversationRepository()


@pytest.fixture
def ready() -> dict[str, bool]:
    return {"database": True, "redis": True}


@pytest.fixture
async def client(jwks_transport, audit_log, ready, conv_repo) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    transport, _ = jwks_transport
    settings = Settings(env="test", log_json=False, oidc_issuer=ISSUER, oidc_audience=AUDIENCE)

    def factory(s: Settings) -> Container:
        async def check_db() -> bool:
            return ready["database"]

        async def check_redis() -> bool:
            return ready["redis"]

        plugins = PluginRegistry()
        plugins.load_all()
        audit = AuditService(audit_log)
        agents = AgentService(InMemoryAgentRepository(), PluginSkillCatalog(plugins))
        conversations = ConversationService(conv_repo)
        router = ModelRouter({"fake": FakeChatModel()})
        runs = RunAgentService(
            agents, conversations, ToolGateway(plugins, audit), router, LangGraphReactLoop(), audit
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
            model_router=router,
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
    assert {s["id"] for s in skills} == {"echo", "clock"}
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
