from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from aiagent.adapters.db import Database
from aiagent.modules.conversations.domain import (
    Approval,
    ApprovalStatus,
    Delegation,
    DelegationStatus,
    Message,
    Run,
    RunStatus,
    Thread,
    Usage,
)

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


class _BaseConversationRepository:
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


_INSERT_APPROVAL = text(
    """
    INSERT INTO conversations.approvals
      (id, tenant_id, thread_id, run_id, agent_id, requester_id, tool_use_id, tool, risk, reason,
       arguments, approver_roles, connection_id, status)
    VALUES
      (:id, :tenant_id, :thread_id, :run_id, :agent_id, :requester_id, :tool_use_id, :tool, :risk,
       :reason, CAST(:arguments AS jsonb), CAST(:approver_roles AS jsonb), :connection_id,
       'pending')
    RETURNING id, tenant_id, thread_id, run_id, agent_id, requester_id, tool_use_id, tool, risk,
              reason, arguments, approver_roles, connection_id, status, decided_by,
              decision_note, created_at, decided_at
    """
)
_GET_APPROVAL = text(
    """
    SELECT id, tenant_id, thread_id, run_id, agent_id, requester_id, tool_use_id, tool, risk,
           reason, arguments, approver_roles, connection_id, status, decided_by, decision_note,
           created_at, decided_at
      FROM conversations.approvals WHERE id = :id
    """
)
_LIST_APPROVALS = text(
    """
    SELECT id, tenant_id, thread_id, run_id, agent_id, requester_id, tool_use_id, tool, risk,
           reason, arguments, approver_roles, connection_id, status, decided_by, decision_note,
           created_at, decided_at
      FROM conversations.approvals
     WHERE (CAST(:status AS text) IS NULL OR status = CAST(:status AS text))
       AND (CAST(:requester AS text) IS NULL OR requester_id = CAST(:requester AS text))
       AND (CAST(:thread_id AS uuid) IS NULL OR thread_id = CAST(:thread_id AS uuid))
     ORDER BY created_at DESC
     LIMIT :limit
    """
)
_DECIDE_APPROVAL = text(
    """
    UPDATE conversations.approvals
       SET status = :status, decided_by = :decided_by, decision_note = :note, decided_at = now()
     WHERE id = :id AND status = 'pending'
    RETURNING id, tenant_id, thread_id, run_id, agent_id, requester_id, tool_use_id, tool, risk,
              reason, arguments, approver_roles, connection_id, status, decided_by,
              decision_note, created_at, decided_at
    """
)
_FOR_TOOL_USES = text(
    """
    SELECT id, tenant_id, thread_id, run_id, agent_id, requester_id, tool_use_id, tool, risk,
           reason, arguments, approver_roles, connection_id, status, decided_by, decision_note,
           created_at, decided_at
      FROM conversations.approvals
     WHERE thread_id = :thread_id AND tool_use_id = ANY(:ids)
     ORDER BY created_at DESC
    """
)
_EXPIRE = text(
    """
    UPDATE conversations.approvals
       SET status = 'expired', decided_at = now(), decision_note = 'Hết hiệu lực do tin nhắn mới'
     WHERE thread_id = :thread_id AND status = 'pending'
    """
)


def _approval(m: Any) -> Approval:
    def load(v: Any) -> Any:
        return json.loads(v) if isinstance(v, str) else v

    return Approval(
        id=m["id"],
        tenant_id=m["tenant_id"],
        thread_id=m["thread_id"],
        run_id=m["run_id"],
        agent_id=m["agent_id"],
        requester_id=m["requester_id"],
        tool_use_id=m["tool_use_id"],
        tool=m["tool"],
        risk=m["risk"],
        reason=m["reason"],
        arguments=load(m["arguments"]) or {},
        approver_roles=load(m["approver_roles"]) or [],
        connection_id=m["connection_id"],
        status=ApprovalStatus(m["status"]),
        decided_by=m["decided_by"],
        decision_note=m["decision_note"],
        created_at=m["created_at"],
        decided_at=m["decided_at"],
    )


class _ApprovalMixin:
    _db: Database

    async def create_approvals(
        self,
        tenant_id: str,
        thread_id: UUID,
        run_id: UUID,
        agent_id: UUID,
        requester_id: str,
        requests: list[dict[str, Any]],
    ) -> list[Approval]:
        out: list[Approval] = []
        async with self._db.tenant_session(tenant_id) as s:
            for r in requests:
                row = (
                    await s.execute(
                        _INSERT_APPROVAL,
                        {
                            "id": uuid4(),
                            "tenant_id": tenant_id,
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "agent_id": agent_id,
                            "requester_id": requester_id,
                            "tool_use_id": str(r["tool_use_id"]),
                            "tool": str(r["tool"]),
                            "risk": str(r["risk"]),
                            "reason": str(r.get("reason", ""))[:500],
                            "arguments": json.dumps(
                                r.get("arguments") or {}, ensure_ascii=False, default=str
                            ),
                            "approver_roles": json.dumps(list(r.get("approver_roles") or [])),
                            "connection_id": r.get("connection_id"),
                        },
                    )
                ).one()
                out.append(_approval(row._mapping))
        return out

    async def get_approval(self, tenant_id: str, approval_id: UUID) -> Approval | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET_APPROVAL, {"id": approval_id})).first()
        return _approval(row._mapping) if row else None

    async def list_approvals(
        self,
        tenant_id: str,
        *,
        status: ApprovalStatus | None,
        requester_id: str | None,
        thread_id: UUID | None,
        limit: int,
    ) -> list[Approval]:
        params = {
            "status": status.value if status else None,
            "requester": requester_id,
            "thread_id": thread_id,
            "limit": limit,
        }
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST_APPROVALS, params)).all()
        return [_approval(r._mapping) for r in rows]

    async def decide_approval(
        self,
        tenant_id: str,
        approval_id: UUID,
        status: ApprovalStatus,
        decided_by: str,
        note: str | None,
    ) -> Approval | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (
                await s.execute(
                    _DECIDE_APPROVAL,
                    {
                        "id": approval_id,
                        "status": status.value,
                        "decided_by": decided_by,
                        "note": (note or None) and note[:500],
                    },
                )
            ).first()
        return _approval(row._mapping) if row else None

    async def approvals_for_tool_uses(
        self, tenant_id: str, thread_id: UUID, tool_use_ids: list[str]
    ) -> list[Approval]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (
                await s.execute(_FOR_TOOL_USES, {"thread_id": thread_id, "ids": tool_use_ids})
            ).all()
        return [_approval(r._mapping) for r in rows]

    async def expire_pending(self, tenant_id: str, thread_id: UUID) -> int:
        async with self._db.tenant_session(tenant_id) as s:
            result = await s.execute(_EXPIRE, {"thread_id": thread_id})
        return int(getattr(result, "rowcount", 0) or 0)


_INSERT_DELEGATION = text(
    """
    INSERT INTO conversations.delegations
      (id, tenant_id, parent_thread_id, parent_run_id, tool_use_id, agent_id, task, status)
    VALUES (:id, :tenant_id, :parent_thread_id, :parent_run_id, :tool_use_id, :agent_id, :task,
            'running')
    RETURNING id, tenant_id, parent_thread_id, parent_run_id, tool_use_id, agent_id, task,
              child_thread_id, child_run_id, status, result, tainted, created_at, updated_at
    """
)
_UPDATE_DELEGATION = text(
    """
    UPDATE conversations.delegations
       SET status = :status, child_thread_id = :child_thread_id, child_run_id = :child_run_id,
           result = :result, tainted = :tainted, updated_at = now()
     WHERE id = :id
    """
)
_DELEGATIONS_FOR = text(
    """
    SELECT id, tenant_id, parent_thread_id, parent_run_id, tool_use_id, agent_id, task,
           child_thread_id, child_run_id, status, result, tainted, created_at, updated_at
      FROM conversations.delegations
     WHERE parent_thread_id = :thread_id AND tool_use_id = ANY(:ids)
     ORDER BY created_at DESC
    """
)


def _delegation(m: Any) -> Delegation:
    return Delegation(
        id=m["id"],
        tenant_id=m["tenant_id"],
        parent_thread_id=m["parent_thread_id"],
        parent_run_id=m["parent_run_id"],
        tool_use_id=m["tool_use_id"],
        agent_id=m["agent_id"],
        task=m["task"],
        child_thread_id=m["child_thread_id"],
        child_run_id=m["child_run_id"],
        status=DelegationStatus(m["status"]),
        result=m["result"],
        tainted=bool(m["tainted"]),
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


class _DelegationMixin:
    _db: Database

    async def create_delegation(
        self,
        tenant_id: str,
        parent_thread_id: UUID,
        parent_run_id: UUID,
        tool_use_id: str,
        agent_id: UUID,
        task: str,
    ) -> Delegation:
        async with self._db.tenant_session(tenant_id) as s:
            row = (
                await s.execute(
                    _INSERT_DELEGATION,
                    {
                        "id": uuid4(),
                        "tenant_id": tenant_id,
                        "parent_thread_id": parent_thread_id,
                        "parent_run_id": parent_run_id,
                        "tool_use_id": tool_use_id,
                        "agent_id": agent_id,
                        "task": task[:8000],
                    },
                )
            ).one()
        return _delegation(row._mapping)

    async def update_delegation(
        self,
        tenant_id: str,
        delegation_id: UUID,
        *,
        status: DelegationStatus,
        child_thread_id: UUID | None,
        child_run_id: UUID | None,
        result: str | None,
        tainted: bool,
    ) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(
                _UPDATE_DELEGATION,
                {
                    "id": delegation_id,
                    "status": status.value,
                    "child_thread_id": child_thread_id,
                    "child_run_id": child_run_id,
                    "result": result,
                    "tainted": tainted,
                },
            )

    async def delegations_for_tool_uses(
        self, tenant_id: str, parent_thread_id: UUID, tool_use_ids: list[str]
    ) -> list[Delegation]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (
                await s.execute(
                    _DELEGATIONS_FOR, {"thread_id": parent_thread_id, "ids": tool_use_ids}
                )
            ).all()
        return [_delegation(r._mapping) for r in rows]


class SqlConversationRepository(_BaseConversationRepository, _ApprovalMixin, _DelegationMixin):
    """Thread, message, run và approval trên Postgres (RLS theo tenant)."""
