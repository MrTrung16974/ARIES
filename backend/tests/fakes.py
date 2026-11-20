"""Repository in-memory cho unit test (cùng hành vi quan sát được với bản SQL)."""

from __future__ import annotations

import itertools
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from aiagent.modules.agents.domain import Agent, AgentSpec, AgentStatus, AgentVersion, CreatedVia
from aiagent.modules.audit.domain import GENESIS_HASH, AuditEvent, AuditRecord, compute_hash
from aiagent.modules.builder.domain import BuilderSession, SessionStatus
from aiagent.modules.conversations.domain import Message, Run, RunStatus, Thread
from aiagent.modules.identity.domain import Principal, Role, UserProfile
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
        self.runs[run.id] = {"status": RunStatus.RUNNING}
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
        self.runs[run_id] = {"status": status, "usage": usage, "cost": cost_usd, "error": error}


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
