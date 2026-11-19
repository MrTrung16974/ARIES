from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from aiagent.adapters.db import Database
from aiagent.modules.audit.domain import (
    GENESIS_HASH,
    AuditEvent,
    AuditRecord,
    Outcome,
    compute_hash,
)

# Khóa advisory theo tenant để các bản ghi đồng thời nối chuỗi tuần tự.
_LOCK = text("SELECT pg_advisory_xact_lock(hashtext('audit:' || :tenant_id))")
_LAST_HASH = text(
    "SELECT hash FROM audit.audit_events WHERE tenant_id = :tenant_id ORDER BY seq DESC LIMIT 1"
)
_INSERT = text(
    """
    INSERT INTO audit.audit_events
      (event_id, tenant_id, occurred_at, actor_id, action, outcome,
       resource_type, resource_id, ip, details, prev_hash, hash)
    VALUES
      (:event_id, :tenant_id, :occurred_at, :actor_id, :action, :outcome,
       :resource_type, :resource_id, :ip, CAST(:details AS jsonb), :prev_hash, :hash)
    RETURNING seq, event_id, tenant_id, occurred_at, actor_id, action, outcome,
              resource_type, resource_id, ip, details, prev_hash, hash
    """
)
_SELECT_RECENT = text(
    """
    SELECT seq, event_id, tenant_id, occurred_at, actor_id, action, outcome,
           resource_type, resource_id, ip, details, prev_hash, hash
      FROM audit.audit_events
     WHERE tenant_id = :tenant_id
       AND (CAST(:before_seq AS bigint) IS NULL OR seq < CAST(:before_seq AS bigint))
     ORDER BY seq DESC
     LIMIT :limit
    """
)
_SELECT_ALL_ASC = text(
    """
    SELECT seq, event_id, tenant_id, occurred_at, actor_id, action, outcome,
           resource_type, resource_id, ip, details, prev_hash, hash
      FROM audit.audit_events
     WHERE tenant_id = :tenant_id
     ORDER BY seq
    """
)


class SqlAuditLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def append(self, event: AuditEvent) -> AuditRecord:
        async with self._db.tenant_session(event.tenant_id) as session:
            await session.execute(_LOCK, {"tenant_id": event.tenant_id})
            prev = (await session.execute(_LAST_HASH, {"tenant_id": event.tenant_id})).scalar()
            prev_hash = prev or GENESIS_HASH
            row = (
                await session.execute(
                    _INSERT,
                    {
                        "event_id": event.event_id,
                        "tenant_id": event.tenant_id,
                        "occurred_at": event.occurred_at,
                        "actor_id": event.actor_id,
                        "action": event.action,
                        "outcome": event.outcome.value,
                        "resource_type": event.resource_type,
                        "resource_id": event.resource_id,
                        "ip": event.ip,
                        "details": json.dumps(event.details, ensure_ascii=False, default=str),
                        "prev_hash": prev_hash,
                        "hash": compute_hash(prev_hash, event),
                    },
                )
            ).one()
        return _to_record(row._mapping)

    async def list_recent(
        self, tenant_id: str, *, limit: int = 50, before_seq: int | None = None
    ) -> list[AuditRecord]:
        params = {"tenant_id": tenant_id, "limit": limit, "before_seq": before_seq}
        async with self._db.tenant_session(tenant_id) as session:
            rows = (await session.execute(_SELECT_RECENT, params)).all()
        return [_to_record(r._mapping) for r in rows]

    async def list_all_ascending(self, tenant_id: str) -> list[AuditRecord]:
        async with self._db.tenant_session(tenant_id) as session:
            rows = (await session.execute(_SELECT_ALL_ASC, {"tenant_id": tenant_id})).all()
        return [_to_record(r._mapping) for r in rows]


def _to_record(m: Any) -> AuditRecord:
    details = m["details"]
    if isinstance(details, str):
        details = json.loads(details)
    return AuditRecord(
        seq=m["seq"],
        prev_hash=m["prev_hash"],
        hash=m["hash"],
        event=AuditEvent(
            event_id=m["event_id"],
            tenant_id=m["tenant_id"],
            occurred_at=m["occurred_at"],
            actor_id=m["actor_id"],
            action=m["action"],
            outcome=Outcome(m["outcome"]),
            resource_type=m["resource_type"],
            resource_id=m["resource_id"],
            ip=m["ip"],
            details=details or {},
        ),
    )
