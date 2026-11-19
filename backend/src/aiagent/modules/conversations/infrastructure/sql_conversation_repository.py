from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from aiagent.adapters.db import Database
from aiagent.modules.conversations.domain import Message, Run, RunStatus, Thread, Usage

_INSERT_THREAD = text(
    """
    INSERT INTO conversations.threads
      (id, tenant_id, user_id, agent_id, agent_version, is_draft, title)
    VALUES (:id, :tenant_id, :user_id, :agent_id, :agent_version, :is_draft, :title)
    RETURNING id, tenant_id, user_id, agent_id, agent_version, is_draft, title,
              created_at, updated_at
    """
)
_GET_THREAD = text(
    """
    SELECT id, tenant_id, user_id, agent_id, agent_version, is_draft, title, created_at, updated_at
      FROM conversations.threads WHERE id = :id
    """
)
_LIST_THREADS = text(
    """
    SELECT id, tenant_id, user_id, agent_id, agent_version, is_draft, title, created_at, updated_at
      FROM conversations.threads
     WHERE user_id = :user_id
       AND (CAST(:agent_id AS uuid) IS NULL OR agent_id = CAST(:agent_id AS uuid))
     ORDER BY updated_at DESC
     LIMIT :limit
    """
)
_LIST_MESSAGES = text(
    """
    SELECT id, thread_id, run_id, role, content, created_at
      FROM conversations.messages WHERE thread_id = :thread_id ORDER BY id
    """
)
_INSERT_MESSAGE = text(
    """
    INSERT INTO conversations.messages (tenant_id, thread_id, run_id, role, content)
    VALUES (:tenant_id, :thread_id, :run_id, :role, CAST(:content AS jsonb))
    RETURNING id, thread_id, run_id, role, content, created_at
    """
)
_TOUCH_THREAD = text("UPDATE conversations.threads SET updated_at = now() WHERE id = :id")
_INSERT_RUN = text(
    """
    INSERT INTO conversations.runs
      (id, tenant_id, thread_id, agent_id, agent_version, user_id, model, status)
    VALUES (:id, :tenant_id, :thread_id, :agent_id, :agent_version, :user_id, :model, 'running')
    """
)
_FINISH_RUN = text(
    """
    UPDATE conversations.runs
       SET status = :status, input_tokens = :input_tokens, output_tokens = :output_tokens,
           cache_read_tokens = :cache_read, cache_write_tokens = :cache_write,
           cost_usd = :cost_usd, error = :error, finished_at = now()
     WHERE id = :id
    """
)


class SqlConversationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_thread(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: UUID,
        agent_version: int,
        is_draft: bool,
        title: str,
    ) -> Thread:
        async with self._db.tenant_session(tenant_id) as s:
            row = (
                await s.execute(
                    _INSERT_THREAD,
                    {
                        "id": uuid4(),
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "agent_id": agent_id,
                        "agent_version": agent_version,
                        "is_draft": is_draft,
                        "title": title[:200],
                    },
                )
            ).one()
        return _thread(row._mapping)

    async def get_thread(self, tenant_id: str, thread_id: UUID) -> Thread | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET_THREAD, {"id": thread_id})).first()
        return _thread(row._mapping) if row else None

    async def list_threads(
        self, tenant_id: str, user_id: str, agent_id: UUID | None, limit: int
    ) -> list[Thread]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (
                await s.execute(
                    _LIST_THREADS,
                    {"user_id": user_id, "agent_id": agent_id, "limit": limit},
                )
            ).all()
        return [_thread(r._mapping) for r in rows]

    async def list_messages(self, tenant_id: str, thread_id: UUID) -> list[Message]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST_MESSAGES, {"thread_id": thread_id})).all()
        return [_message(r._mapping) for r in rows]

    async def append_message(
        self,
        tenant_id: str,
        thread_id: UUID,
        run_id: UUID | None,
        role: str,
        content: list[dict[str, Any]],
    ) -> Message:
        async with self._db.tenant_session(tenant_id) as s:
            row = (
                await s.execute(
                    _INSERT_MESSAGE,
                    {
                        "tenant_id": tenant_id,
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "role": role,
                        "content": json.dumps(content, ensure_ascii=False),
                    },
                )
            ).one()
            await s.execute(_TOUCH_THREAD, {"id": thread_id})
        return _message(row._mapping)

    async def start_run(
        self,
        tenant_id: str,
        thread_id: UUID,
        agent_id: UUID,
        agent_version: int,
        user_id: str,
        model: str,
    ) -> Run:
        run_id = uuid4()
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(
                _INSERT_RUN,
                {
                    "id": run_id,
                    "tenant_id": tenant_id,
                    "thread_id": thread_id,
                    "agent_id": agent_id,
                    "agent_version": agent_version,
                    "user_id": user_id,
                    "model": model,
                },
            )
        return Run(id=run_id, thread_id=thread_id, status=RunStatus.RUNNING, model=model)

    async def finish_run(
        self,
        tenant_id: str,
        run_id: UUID,
        status: RunStatus,
        usage: Usage,
        cost_usd: float,
        error: str | None,
    ) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(
                _FINISH_RUN,
                {
                    "id": run_id,
                    "status": status.value,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read": usage.cache_read_input_tokens,
                    "cache_write": usage.cache_creation_input_tokens,
                    "cost_usd": cost_usd,
                    "error": (error or None) and error[:2000],
                },
            )


def _thread(m: Any) -> Thread:
    return Thread(
        id=m["id"],
        tenant_id=m["tenant_id"],
        user_id=m["user_id"],
        agent_id=m["agent_id"],
        agent_version=m["agent_version"],
        is_draft=m["is_draft"],
        title=m["title"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def _message(m: Any) -> Message:
    content = m["content"]
    if isinstance(content, str):
        content = json.loads(content)
    return Message(
        id=m["id"],
        thread_id=m["thread_id"],
        run_id=m["run_id"],
        role=m["role"],
        content=content,
        created_at=m["created_at"],
    )
