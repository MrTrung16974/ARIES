from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from aiagent.adapters.db import Database
from aiagent.modules.builder.domain import BuilderSession, SessionStatus

_INSERT = text(
    """
    INSERT INTO builder.sessions (id, tenant_id, user_id, agent_id, base_version, status, draft_spec)
    VALUES (:id, :tenant_id, :user_id, :agent_id, :base_version, :status,
            CAST(:draft_spec AS jsonb))
    RETURNING id, tenant_id, user_id, agent_id, base_version, status, brief, draft_spec,
              questions, clarify_rounds, eval_report, created_at, updated_at
    """
)
_GET = text(
    """
    SELECT id, tenant_id, user_id, agent_id, base_version, status, brief, draft_spec, questions,
           clarify_rounds, eval_report, created_at, updated_at
      FROM builder.sessions WHERE id = :id
    """
)
_LIST = text(
    """
    SELECT id, tenant_id, user_id, agent_id, base_version, status, brief, draft_spec, questions,
           clarify_rounds, eval_report, created_at, updated_at
      FROM builder.sessions WHERE user_id = :user_id ORDER BY updated_at DESC LIMIT :limit
    """
)
_SAVE = text(
    """
    UPDATE builder.sessions
       SET agent_id = :agent_id, base_version = :base_version, status = :status,
           brief = CAST(:brief AS jsonb), draft_spec = CAST(:draft_spec AS jsonb),
           questions = CAST(:questions AS jsonb), clarify_rounds = :clarify_rounds,
           eval_report = CAST(:eval_report AS jsonb), updated_at = now()
     WHERE id = :id
    """
)
_ADD_MESSAGE = text(
    """
    INSERT INTO builder.messages (tenant_id, session_id, role, content, events)
    VALUES (:tenant_id, :session_id, :role, :content, CAST(:events AS jsonb))
    """
)
_MESSAGES = text(
    """
    SELECT role, content, events, created_at FROM builder.messages
     WHERE session_id = :session_id ORDER BY id
    """
)


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class SqlBuilderSessionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: UUID | None,
        base_version: int | None,
        draft_spec: dict[str, Any] | None,
    ) -> BuilderSession:
        async with self._db.tenant_session(tenant_id) as s:
            row = (
                await s.execute(
                    _INSERT,
                    {
                        "id": uuid4(),
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "agent_id": agent_id,
                        "base_version": base_version,
                        "status": SessionStatus.DRAFTING.value,
                        "draft_spec": _dump(draft_spec),
                    },
                )
            ).one()
        return _session(row._mapping)

    async def get(self, tenant_id: str, session_id: UUID) -> BuilderSession | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET, {"id": session_id})).first()
        return _session(row._mapping) if row else None

    async def list_for_user(self, tenant_id: str, user_id: str, limit: int) -> list[BuilderSession]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST, {"user_id": user_id, "limit": limit})).all()
        return [_session(r._mapping) for r in rows]

    async def save(self, session: BuilderSession) -> None:
        async with self._db.tenant_session(session.tenant_id) as s:
            await s.execute(
                _SAVE,
                {
                    "id": session.id,
                    "agent_id": session.agent_id,
                    "base_version": session.base_version,
                    "status": session.status.value,
                    "brief": _dump(session.brief),
                    "draft_spec": _dump(session.draft_spec),
                    "questions": _dump(session.questions) or "[]",
                    "clarify_rounds": session.clarify_rounds,
                    "eval_report": _dump(session.eval_report),
                },
            )

    async def add_message(
        self,
        tenant_id: str,
        session_id: UUID,
        role: str,
        content: str,
        events: list[dict[str, Any]],
    ) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(
                _ADD_MESSAGE,
                {
                    "tenant_id": tenant_id,
                    "session_id": session_id,
                    "role": role,
                    "content": content,
                    "events": _dump(events) or "[]",
                },
            )

    async def messages(self, tenant_id: str, session_id: UUID) -> list[dict[str, Any]]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_MESSAGES, {"session_id": session_id})).all()
        return [
            {
                "role": r.role,
                "content": r.content,
                "events": _load(r.events) or [],
                "created_at": r.created_at,
            }
            for r in rows
        ]


def _session(m: Any) -> BuilderSession:
    return BuilderSession(
        id=m["id"],
        tenant_id=m["tenant_id"],
        user_id=m["user_id"],
        agent_id=m["agent_id"],
        base_version=m["base_version"],
        status=SessionStatus(m["status"]),
        brief=_load(m["brief"]),
        draft_spec=_load(m["draft_spec"]),
        questions=_load(m["questions"]) or [],
        clarify_rounds=m["clarify_rounds"],
        eval_report=_load(m["eval_report"]),
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )
