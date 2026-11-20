from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aiagent.adapters.db import Database
from aiagent.modules.connections.domain import Connection, ConnectionScope, ConnectionStatus
from aiagent.shared_kernel.errors import ConflictError, NotFoundError

_INSERT = text(
    """
    INSERT INTO connections.connections
      (id, tenant_id, name, connector_id, scope, owner_id, config, secret_keys, status)
    VALUES (:id, :tenant_id, :name, :connector_id, :scope, :owner_id, CAST(:config AS jsonb),
            CAST(:secret_keys AS jsonb), :status)
    RETURNING id, tenant_id, name, connector_id, scope, owner_id, config, secret_keys, status,
           last_test_ok, last_test_note, last_test_at, created_at, updated_at
    """
)
_GET = text(
    """
    SELECT id, tenant_id, name, connector_id, scope, owner_id, config, secret_keys, status,
           last_test_ok, last_test_note, last_test_at, created_at, updated_at
      FROM connections.connections WHERE id = :id
    """
)
_LIST = text(
    """
    SELECT id, tenant_id, name, connector_id, scope, owner_id, config, secret_keys, status,
           last_test_ok, last_test_note, last_test_at, created_at, updated_at
      FROM connections.connections
     WHERE status <> 'deleted' ORDER BY name
    """
)
_SAVE = text(
    """
    UPDATE connections.connections
       SET name = :name, config = CAST(:config AS jsonb), secret_keys = CAST(:secret_keys AS jsonb),
           status = :status, last_test_ok = :last_test_ok, last_test_note = :last_test_note,
           last_test_at = :last_test_at, updated_at = now()
     WHERE id = :id
    RETURNING id, tenant_id, name, connector_id, scope, owner_id, config, secret_keys, status,
           last_test_ok, last_test_note, last_test_at, created_at, updated_at
    """
)


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _row(m: Any) -> Connection:
    return Connection(
        id=m["id"],
        tenant_id=m["tenant_id"],
        name=m["name"],
        connector_id=m["connector_id"],
        scope=ConnectionScope(m["scope"]),
        owner_id=m["owner_id"],
        config=_load(m["config"]) or {},
        secret_keys=list(_load(m["secret_keys"]) or []),
        status=ConnectionStatus(m["status"]),
        last_test_ok=m["last_test_ok"],
        last_test_note=m["last_test_note"],
        last_test_at=m["last_test_at"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


class SqlConnectionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, connection: Connection) -> Connection:
        c = connection
        try:
            async with self._db.tenant_session(c.tenant_id) as s:
                row = (
                    await s.execute(
                        _INSERT,
                        {
                            "id": c.id,
                            "tenant_id": c.tenant_id,
                            "name": c.name,
                            "connector_id": c.connector_id,
                            "scope": c.scope.value,
                            "owner_id": c.owner_id,
                            "config": json.dumps(c.config, ensure_ascii=False),
                            "secret_keys": json.dumps(c.secret_keys),
                            "status": c.status.value,
                        },
                    )
                ).one()
        except IntegrityError as exc:
            raise ConflictError("Tên kết nối đã tồn tại.") from exc
        return _row(row._mapping)

    async def get(self, tenant_id: str, connection_id: UUID) -> Connection | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET, {"id": connection_id})).first()
        return _row(row._mapping) if row else None

    async def list_all(self, tenant_id: str) -> list[Connection]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST)).all()
        return [_row(r._mapping) for r in rows]

    async def save(self, connection: Connection) -> Connection:
        c = connection
        try:
            async with self._db.tenant_session(c.tenant_id) as s:
                row = (
                    await s.execute(
                        _SAVE,
                        {
                            "id": c.id,
                            "name": c.name,
                            "config": json.dumps(c.config, ensure_ascii=False),
                            "secret_keys": json.dumps(c.secret_keys),
                            "status": c.status.value,
                            "last_test_ok": c.last_test_ok,
                            "last_test_note": c.last_test_note,
                            "last_test_at": c.last_test_at,
                        },
                    )
                ).first()
        except IntegrityError as exc:
            raise ConflictError("Tên kết nối đã tồn tại.") from exc
        if row is None:
            raise NotFoundError("Không tìm thấy kết nối.")
        return _row(row._mapping)
