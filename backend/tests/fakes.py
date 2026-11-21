"""Repository in-memory cho unit test (cùng hành vi quan sát được với bản SQL)."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import replace
from importlib.metadata import entry_points
from typing import Any
from uuid import UUID, uuid4

from aiagent.modules.agents.domain import Agent, AgentSpec, AgentStatus, AgentVersion, CreatedVia
from aiagent.modules.audit.domain import GENESIS_HASH, AuditEvent, AuditRecord, compute_hash
from aiagent.modules.builder.domain import BuilderSession, SessionStatus
from aiagent.modules.connections.domain import Connection, ConnectionStatus
from aiagent.modules.conversations.domain import (
    Approval,
    ApprovalStatus,
    Delegation,
    DelegationStatus,
    Feedback,
    Message,
    Run,
    RunStatus,
    Thread,
)
from aiagent.modules.identity.domain import Principal, Role, UserProfile
from aiagent.modules.mcp.domain import McpServer
from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    ConnectionContext,
    HealthStatus,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import ConflictError, NotFoundError
from aiagent.shared_kernel.usage import Usage


def principal(sub: str = "user-1", *roles: str, tenant: str = "default") -> Principal:
    return Principal(sub, tenant, sub, None, frozenset(Role(r) for r in (roles or ("user",))))


class InMemoryAuditLog:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def append(self, event: AuditEvent) -> AuditRecord:
        prev = self.records[-1].hash if self.records else GENESIS_HASH
        rec = AuditRecord(len(self.records) + 1, event, prev, compute_hash(prev, event))
        self.records.append(rec)
        return rec

    async def list_recent(
        self, tenant_id: str, *, limit: int = 50, before_seq: int | None = None
    ) -> list[AuditRecord]:
        return list(reversed(self.records))[:limit]

    async def list_all_ascending(self, tenant_id: str) -> list[AuditRecord]:
        return list(self.records)

    def actions(self) -> list[str]:
        return [r.event.action for r in self.records]


class FakeUsers:
    async def touch(self, p: Principal) -> UserProfile:
        now = utcnow()
        return UserProfile(p.tenant_id, p.subject, p.username, p.email, now, now)


class InMemoryAgentRepository:
    def __init__(self) -> None:
        self.agents: dict[UUID, Agent] = {}
        self.versions: dict[tuple[UUID, int], AgentVersion] = {}

    async def create(
        self, tenant_id: str, owner_id: str, spec: AgentSpec, via: CreatedVia, note: str
    ) -> tuple[Agent, AgentVersion]:
        now = utcnow()
        agent = Agent(
            uuid4(),
            tenant_id,
            spec.name,
            spec.description,
            owner_id,
            AgentStatus.DRAFT,
            1,
            None,
            now,
            now,
        )
        version = AgentVersion(agent.id, 1, spec, owner_id, via, note, now)
        self.agents[agent.id] = agent
        self.versions[(agent.id, 1)] = version
        return agent, version

    async def add_version(
        self,
        tenant_id: str,
        agent_id: UUID,
        spec: AgentSpec,
        created_by: str,
        via: CreatedVia,
        note: str,
        expected_latest: int | None,
    ) -> AgentVersion:
        agent = self._get(tenant_id, agent_id)
        if expected_latest is not None and agent.latest_version != expected_latest:
            raise ConflictError("conflict")
        number = agent.latest_version + 1
        self.agents[agent_id] = replace(agent, latest_version=number, name=spec.name)
        version = AgentVersion(agent_id, number, spec, created_by, via, note, utcnow())
        self.versions[(agent_id, number)] = version
        return version

    async def get(self, tenant_id: str, agent_id: UUID) -> Agent | None:
        agent = self.agents.get(agent_id)
        return agent if agent and agent.tenant_id == tenant_id else None

    async def list_all(self, tenant_id: str, *, include_archived: bool = False) -> list[Agent]:
        return [a for a in self.agents.values() if a.tenant_id == tenant_id]

    async def get_version(
        self, tenant_id: str, agent_id: UUID, version: int
    ) -> AgentVersion | None:
        if await self.get(tenant_id, agent_id) is None:
            return None
        return self.versions.get((agent_id, version))

    async def list_versions(self, tenant_id: str, agent_id: UUID) -> list[AgentVersion]:
        return sorted(
            (v for (a, _), v in self.versions.items() if a == agent_id),
            key=lambda v: -v.version,
        )

    async def set_published(self, tenant_id: str, agent_id: UUID, version: int | None) -> Agent:
        agent = self._get(tenant_id, agent_id)
        status = AgentStatus.DRAFT if version is None else AgentStatus.PUBLISHED
        self.agents[agent_id] = replace(agent, published_version=version, status=status)
        return self.agents[agent_id]

    async def set_archived(self, tenant_id: str, agent_id: UUID, archived: bool) -> Agent:
        agent = self._get(tenant_id, agent_id)
        self.agents[agent_id] = replace(agent, status=AgentStatus.ARCHIVED)
        return self.agents[agent_id]

    def _get(self, tenant_id: str, agent_id: UUID) -> Agent:
        agent = self.agents.get(agent_id)
        if agent is None or agent.tenant_id != tenant_id:
            raise NotFoundError()
        return agent


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self.threads: dict[UUID, Thread] = {}
        self.messages: list[Message] = []
        self.runs: dict[UUID, dict[str, Any]] = {}
        self.approvals: list[Approval] = []
        self._ids = itertools.count(1)

    async def create_thread(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: UUID,
        agent_version: int,
        is_draft: bool,
        title: str,
    ) -> Thread:
        now = utcnow()
        thread = Thread(
            uuid4(), tenant_id, user_id, agent_id, agent_version, is_draft, title, now, now
        )
        self.threads[thread.id] = thread
        return thread

    async def get_thread(self, tenant_id: str, thread_id: UUID) -> Thread | None:
        t = self.threads.get(thread_id)
        return t if t and t.tenant_id == tenant_id else None

    async def list_threads(
        self, tenant_id: str, user_id: str, agent_id: UUID | None, limit: int
    ) -> list[Thread]:
        return [
            t
            for t in self.threads.values()
            if t.user_id == user_id and (agent_id is None or t.agent_id == agent_id)
        ][:limit]

    async def list_messages(self, tenant_id: str, thread_id: UUID) -> list[Message]:
        return [m for m in self.messages if m.thread_id == thread_id]

    async def append_message(
        self,
        tenant_id: str,
        thread_id: UUID,
        run_id: UUID | None,
        role: str,
        content: list[dict[str, Any]],
    ) -> Message:
        message = Message(next(self._ids), thread_id, run_id, role, content, utcnow())
        self.messages.append(message)
        return message

    async def start_run(
        self,
        tenant_id: str,
        thread_id: UUID,
        agent_id: UUID,
        agent_version: int,
        user_id: str,
        model: str,
    ) -> Run:
        run = Run(uuid4(), thread_id, RunStatus.RUNNING, model)
        self.runs[run.id] = {
            "status": RunStatus.RUNNING,
            "thread_id": thread_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "started_at": utcnow(),
        }
        return run

    async def finish_run(
        self,
        tenant_id: str,
        run_id: UUID,
        status: RunStatus,
        usage: Usage,
        cost_usd: float,
        error: str | None,
    ) -> None:
        self.runs[run_id] = {
            **self.runs.get(run_id, {}),
            "status": status,
            "usage": usage,
            "cost": cost_usd,
            "error": error,
        }

    # ------------------------------------------------------------ approvals
    async def create_approvals(
        self,
        tenant_id: str,
        thread_id: UUID,
        run_id: UUID,
        agent_id: UUID,
        requester_id: str,
        requests: list[dict[str, Any]],
    ) -> list[Approval]:
        out = []
        for r in requests:
            a = Approval(
                id=uuid4(),
                tenant_id=tenant_id,
                thread_id=thread_id,
                run_id=run_id,
                agent_id=agent_id,
                requester_id=requester_id,
                tool_use_id=str(r["tool_use_id"]),
                tool=str(r["tool"]),
                risk=str(r["risk"]),
                reason=str(r.get("reason", "")),
                arguments=dict(r.get("arguments") or {}),
                approver_roles=list(r.get("approver_roles") or []),
                connection_id=r.get("connection_id"),
                status=ApprovalStatus.PENDING,
                decided_by=None,
                decision_note=None,
                created_at=utcnow(),
                decided_at=None,
            )
            self.approvals.insert(0, a)
            out.append(a)
        return out

    async def get_approval(self, tenant_id: str, approval_id: UUID) -> Approval | None:
        return next((a for a in self.approvals if a.id == approval_id), None)

    async def list_approvals(
        self,
        tenant_id: str,
        *,
        status: ApprovalStatus | None,
        requester_id: str | None,
        thread_id: UUID | None,
        limit: int,
    ) -> list[Approval]:
        return [
            a
            for a in self.approvals
            if (status is None or a.status is status)
            and (requester_id is None or a.requester_id == requester_id)
            and (thread_id is None or a.thread_id == thread_id)
        ][:limit]

    async def decide_approval(
        self,
        tenant_id: str,
        approval_id: UUID,
        status: ApprovalStatus,
        decided_by: str,
        note: str | None,
    ) -> Approval | None:
        for i, a in enumerate(self.approvals):
            if a.id == approval_id and a.status is ApprovalStatus.PENDING:
                self.approvals[i] = replace(
                    a,
                    status=status,
                    decided_by=decided_by,
                    decision_note=note,
                    decided_at=utcnow(),
                )
                return self.approvals[i]
        return None

    async def approvals_for_tool_uses(
        self, tenant_id: str, thread_id: UUID, tool_use_ids: list[str]
    ) -> list[Approval]:
        return [
            a for a in self.approvals if a.thread_id == thread_id and a.tool_use_id in tool_use_ids
        ]

    async def expire_pending(self, tenant_id: str, thread_id: UUID) -> int:
        n = 0
        for i, a in enumerate(self.approvals):
            if a.thread_id == thread_id and a.status is ApprovalStatus.PENDING:
                self.approvals[i] = replace(a, status=ApprovalStatus.EXPIRED, decided_at=utcnow())
                n += 1
        return n


class InMemoryBuilderSessionRepository:
    def __init__(self) -> None:
        self.sessions: dict[UUID, Any] = {}
        self.history: dict[UUID, list[dict[str, Any]]] = {}

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: UUID | None,
        base_version: int | None,
        draft_spec: dict[str, Any] | None,
    ) -> Any:
        now = utcnow()
        session = BuilderSession(
            uuid4(),
            tenant_id,
            user_id,
            agent_id,
            base_version,
            SessionStatus.DRAFTING,
            None,
            draft_spec,
            [],
            0,
            None,
            now,
            now,
        )
        self.sessions[session.id] = session
        self.history[session.id] = []
        return session

    async def get(self, tenant_id: str, session_id: UUID) -> Any:
        s = self.sessions.get(session_id)
        return replace(s) if s and s.tenant_id == tenant_id else None

    async def list_for_user(self, tenant_id: str, user_id: str, limit: int) -> list[Any]:
        return [s for s in self.sessions.values() if s.user_id == user_id][:limit]

    async def save(self, session: Any) -> None:
        self.sessions[session.id] = replace(session)

    async def add_message(
        self,
        tenant_id: str,
        session_id: UUID,
        role: str,
        content: str,
        events: list[dict[str, Any]],
    ) -> None:
        self.history[session_id].append(
            {"role": role, "content": content, "events": events, "created_at": utcnow()}
        )

    async def messages(self, tenant_id: str, session_id: UUID) -> list[dict[str, Any]]:
        return list(self.history.get(session_id, []))


# ---------------------------------------------------------------- plugin rủi ro cho test HITL
class NotifySkill(Plugin):
    """Skill test: `notify.send` (external_send — luôn cần duyệt), `notify.drop` (destructive)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    @property
    def manifest(self) -> PluginManifest:
        schema = {"type": "object", "properties": {"to": {"type": "string"}}, "required": ["to"]}
        return PluginManifest(
            id="notify",
            kind=PluginKind.SKILL,
            name="Notify",
            version="1.0.0",
            api_version=PLUGIN_API_VERSION,
            tools=(
                ToolSpec(
                    name="notify.send",
                    description="gửi",
                    risk=RiskLevel.EXTERNAL_SEND,
                    input_schema=schema,
                ),
                ToolSpec(name="notify.drop", description="xóa", risk=RiskLevel.DESTRUCTIVE),
            ),
        )

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.sent.append({"tool": name, **arguments})
        return ToolResult(f"đã gửi tới {arguments.get('to')}")


class _StaticEntryPoint:
    def __init__(self, name: str, group: str, obj: object) -> None:
        self.name, self.group, self.value, self._obj = name, group, f"tests:{name}", obj

    def load(self) -> object:
        return self._obj


def source_with(*plugins: Plugin) -> Callable[[str], list[Any]]:
    """Entry point mặc định + plugin test (theo nhóm của manifest)."""

    def source(group: str) -> list[Any]:
        eps: list[Any] = list(entry_points(group=group))
        eps += [
            _StaticEntryPoint(p.manifest.id, group, p)
            for p in plugins
            if p.manifest.entry_point_group == group
        ]
        return eps

    return source


class FakeDbConnector(Plugin):
    """Connector test: cần secret `password`; `fakedb.query` trả dữ liệu không tin cậy."""

    def __init__(self) -> None:
        self.seen_secrets: list[str] = []

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id="fakedb",
            kind=PluginKind.CONNECTOR,
            name="Fake DB",
            version="1.0.0",
            api_version=PLUGIN_API_VERSION,
            config_schema={
                "type": "object",
                "properties": {"host": {"type": "string"}, "port": {"type": "integer"}},
                "required": ["host"],
                "additionalProperties": False,
            },
            secrets=("password",),
            tools=(
                ToolSpec(
                    name="fakedb.query",
                    description="đọc",
                    risk=RiskLevel.READ,
                    untrusted_output=True,
                ),
                ToolSpec(name="fakedb.exec", description="ghi", risk=RiskLevel.WRITE),
            ),
        )

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        assert ctx.connection is not None
        self.seen_secrets.append(ctx.connection.secrets.get("password", ""))
        return ToolResult(f"{name}@{ctx.connection.config['host']}: hàng 1")

    async def test_connection(self, connection: ConnectionContext) -> HealthStatus:
        ok = connection.secrets.get("password") == "pw"
        return HealthStatus(ok, "OK" if ok else "Sai mật khẩu")


class InMemoryVault:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, str]] = {}

    async def put(self, path: str, data: dict[str, str]) -> None:
        self.data[path] = dict(data)

    async def get(self, path: str) -> dict[str, str]:
        return dict(self.data.get(path, {}))

    async def delete(self, path: str) -> None:
        self.data.pop(path, None)


class InMemoryConnectionRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, Connection] = {}

    async def create(self, connection: Connection) -> Connection:
        if any(
            c.name == connection.name
            and c.tenant_id == connection.tenant_id
            and c.status is not ConnectionStatus.DELETED
            for c in self.items.values()
        ):
            raise ConflictError("Tên kết nối đã tồn tại.")
        self.items[connection.id] = connection
        return connection

    async def get(self, tenant_id: str, connection_id: UUID) -> Connection | None:
        c = self.items.get(connection_id)
        return c if c and c.tenant_id == tenant_id else None

    async def list_all(self, tenant_id: str) -> list[Connection]:
        return [
            c
            for c in self.items.values()
            if c.tenant_id == tenant_id and c.status is not ConnectionStatus.DELETED
        ]

    async def save(self, connection: Connection) -> Connection:
        self.items[connection.id] = connection
        return connection


def _add_delegation_methods() -> None:
    """Gắn method delegation vào InMemoryConversationRepository (Phase 3c)."""

    async def create_delegation(
        self: InMemoryConversationRepository,
        tenant_id: str,
        parent_thread_id: UUID,
        parent_run_id: UUID,
        tool_use_id: str,
        agent_id: UUID,
        task: str,
    ) -> Delegation:
        now = utcnow()
        d = Delegation(
            id=uuid4(),
            tenant_id=tenant_id,
            parent_thread_id=parent_thread_id,
            parent_run_id=parent_run_id,
            tool_use_id=tool_use_id,
            agent_id=agent_id,
            task=task,
            child_thread_id=None,
            child_run_id=None,
            status=DelegationStatus.RUNNING,
            result=None,
            tainted=False,
            created_at=now,
            updated_at=now,
        )
        self.__dict__.setdefault("delegations", []).insert(0, d)
        return d

    async def update_delegation(
        self: InMemoryConversationRepository,
        tenant_id: str,
        delegation_id: UUID,
        *,
        status: DelegationStatus,
        child_thread_id: UUID | None,
        child_run_id: UUID | None,
        result: str | None,
        tainted: bool,
    ) -> None:
        items = self.__dict__.setdefault("delegations", [])
        for i, d in enumerate(items):
            if d.id == delegation_id:
                items[i] = replace(
                    d,
                    status=status,
                    child_thread_id=child_thread_id,
                    child_run_id=child_run_id,
                    result=result,
                    tainted=tainted,
                    updated_at=utcnow(),
                )

    async def delegations_for_tool_uses(
        self: InMemoryConversationRepository,
        tenant_id: str,
        parent_thread_id: UUID,
        tool_use_ids: list[str],
    ) -> list[Delegation]:
        return [
            d
            for d in self.__dict__.setdefault("delegations", [])
            if d.parent_thread_id == parent_thread_id and d.tool_use_id in tool_use_ids
        ]

    InMemoryConversationRepository.create_delegation = create_delegation  # type: ignore[attr-defined]
    InMemoryConversationRepository.update_delegation = update_delegation  # type: ignore[attr-defined]
    InMemoryConversationRepository.delegations_for_tool_uses = delegations_for_tool_uses  # type: ignore[attr-defined]


_add_delegation_methods()


class InMemoryMcpServerRepository:
    def __init__(self) -> None:
        self.items: dict[UUID, McpServer] = {}

    async def create(self, server: McpServer) -> McpServer:
        if any(
            s.slug == server.slug and s.tenant_id == server.tenant_id and not s.deleted
            for s in self.items.values()
        ):
            raise ConflictError("Mã MCP server đã tồn tại.")
        self.items[server.id] = server
        return server

    async def get(self, tenant_id: str, server_id: UUID) -> McpServer | None:
        s = self.items.get(server_id)
        return s if s and s.tenant_id == tenant_id else None

    async def list_all(self, tenant_id: str) -> list[McpServer]:
        return [s for s in self.items.values() if s.tenant_id == tenant_id and not s.deleted]

    async def save(self, server: McpServer) -> McpServer:
        self.items[server.id] = server
        return server


class MemoryOAuthStates:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    async def put(self, state: str, data: dict[str, Any], ttl_s: int) -> None:
        self.data[state] = dict(data)

    async def pop(self, state: str) -> dict[str, Any] | None:
        return self.data.pop(state, None)


class FakeOAuthClient:
    """Nhà cung cấp OAuth giả: code `good` hợp lệ; ghi lại verifier để kiểm PKCE."""

    def __init__(self) -> None:
        self.exchanges: list[dict[str, str]] = []
        self.refreshes = 0

    async def exchange(
        self, provider: Any, code: str, code_verifier: str, redirect_uri: str
    ) -> Any:
        from aiagent.modules.connections.domain import TokenSet
        from aiagent.shared_kernel.errors import AppError

        self.exchanges.append({"code": code, "verifier": code_verifier, "redirect": redirect_uri})
        if code != "good":
            raise AppError("Nhà cung cấp từ chối cấp token (invalid_grant).")
        return TokenSet(access_token="at-1", refresh_token="rt-1", expires_at=0.0)  # đã hết hạn

    async def refresh(self, provider: Any, refresh_token: str) -> Any:
        import time

        from aiagent.modules.connections.domain import TokenSet

        self.refreshes += 1
        assert refresh_token == "rt-1"
        return TokenSet(access_token="at-2", refresh_token=None, expires_at=time.time() + 3600)

    async def userinfo(self, provider: Any, access_token: str) -> dict[str, Any]:
        return {"sub": "kc-1", "email": "builder@aries.local"}


def _add_feedback_methods() -> None:
    async def upsert_feedback(
        self: InMemoryConversationRepository,
        tenant_id: str,
        thread_id: UUID,
        message_id: int,
        agent_id: UUID,
        user_id: str,
        rating: int,
        comment: str,
    ) -> Feedback:
        items = self.__dict__.setdefault("feedback", {})
        f = Feedback(
            uuid4(), tenant_id, thread_id, message_id, agent_id, user_id, rating, comment, utcnow()
        )
        items[(message_id, user_id)] = f
        return f

    async def feedback_for_thread(
        self: InMemoryConversationRepository, tenant_id: str, thread_id: UUID
    ) -> list[Feedback]:
        return [
            f for f in self.__dict__.setdefault("feedback", {}).values() if f.thread_id == thread_id
        ]

    InMemoryConversationRepository.upsert_feedback = upsert_feedback  # type: ignore[attr-defined]
    InMemoryConversationRepository.feedback_for_thread = feedback_for_thread  # type: ignore[attr-defined]


_add_feedback_methods()


class MemoryRateLimiter:
    """Cửa sổ cố định trong RAM (giống RedisRateLimiter nhưng không có thời gian)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def hit(self, key: str, limit: int, window_s: int) -> bool:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key] <= limit
