from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aiagent.adapters.db import Database
from aiagent.modules.mcp.domain import McpServer, McpTool, McpTransport
from aiagent.plugin_sdk import RiskLevel
from aiagent.shared_kernel.errors import ConflictError, NotFoundError

_INSERT = text(
    """
    INSERT INTO mcp.servers
      (id, tenant_id, name, slug, transport, url, command_key, secret_keys, created_by)
    VALUES (:id, :tenant_id, :name, :slug, :transport, :url, :command_key,
            CAST(:secret_keys AS jsonb), :created_by)
    RETURNING id, tenant_id, name, slug, transport, url, command_key, secret_keys, enabled,
              deleted, tools, risk_overrides, last_sync_at, last_error, created_by, created_at,
              updated_at
    """
)
_GET = text(
    """
    SELECT id, tenant_id, name, slug, transport, url, command_key, secret_keys, enabled, deleted,
           tools, risk_overrides, last_sync_at, last_error, created_by, created_at, updated_at
      FROM mcp.servers WHERE id = :id
    """
)
_LIST = text(
    """
    SELECT id, tenant_id, name, slug, transport, url, command_key, secret_keys, enabled, deleted,
           tools, risk_overrides, last_sync_at, last_error, created_by, created_at, updated_at
      FROM mcp.servers WHERE NOT deleted ORDER BY name
    """
)
_SAVE = text(
    """
    UPDATE mcp.servers
       SET name = :name, secret_keys = CAST(:secret_keys AS jsonb), enabled = :enabled,
           deleted = :deleted, tools = CAST(:tools AS jsonb),
           risk_overrides = CAST(:risk_overrides AS jsonb), last_sync_at = :last_sync_at,
           last_error = :last_error, updated_at = now()
     WHERE id = :id
    RETURNING id, tenant_id, name, slug, transport, url, command_key, secret_keys, enabled,
              deleted, tools, risk_overrides, last_sync_at, last_error, created_by, created_at,
              updated_at
    """
)


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _row(m: Any) -> McpServer:
    tools = [
        McpTool(
            name=str(t["name"]),
            description=str(t.get("description", "")),
            input_schema=dict(t.get("input_schema") or {"type": "object"}),
            risk=RiskLevel(t.get("risk", "write")),
        )
        for t in _load(m["tools"]) or []
    ]
    return McpServer(
        id=m["id"],
        tenant_id=m["tenant_id"],
        name=m["name"],
        slug=m["slug"],
        transport=McpTransport(m["transport"]),
        url=m["url"],
        command_key=m["command_key"],
        secret_keys=list(_load(m["secret_keys"]) or []),
        enabled=bool(m["enabled"]),
        deleted=bool(m["deleted"]),
        tools=tools,
        risk_overrides=dict(_load(m["risk_overrides"]) or {}),
        last_sync_at=m["last_sync_at"],
        last_error=m["last_error"],
        created_by=m["created_by"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def _tools_json(tools: list[McpTool]) -> str:
    return json.dumps(
        [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "risk": t.risk.value,
            }
            for t in tools
        ],
        ensure_ascii=False,
    )


class SqlMcpServerRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, server: McpServer) -> McpServer:
        s = server
        try:
            async with self._db.tenant_session(s.tenant_id) as session:
                row = (
                    await session.execute(
                        _INSERT,
                        {
                            "id": s.id,
                            "tenant_id": s.tenant_id,
                            "name": s.name,
                            "slug": s.slug,
                            "transport": s.transport.value,
                            "url": s.url,
                            "command_key": s.command_key,
                            "secret_keys": json.dumps(s.secret_keys),
                            "created_by": s.created_by,
                        },
                    )
                ).one()
        except IntegrityError as exc:
            raise ConflictError("Mã MCP server đã tồn tại.") from exc
        return _row(row._mapping)

    async def get(self, tenant_id: str, server_id: UUID) -> McpServer | None:
        async with self._db.tenant_session(tenant_id) as session:
            row = (await session.execute(_GET, {"id": server_id})).first()
        return _row(row._mapping) if row else None

    async def list_all(self, tenant_id: str) -> list[McpServer]:
        async with self._db.tenant_session(tenant_id) as session:
            rows = (await session.execute(_LIST)).all()
        return [_row(r._mapping) for r in rows]

    async def save(self, server: McpServer) -> McpServer:
        s = server
        async with self._db.tenant_session(s.tenant_id) as session:
            row = (
                await session.execute(
                    _SAVE,
                    {
                        "id": s.id,
                        "name": s.name,
                        "secret_keys": json.dumps(s.secret_keys),
                        "enabled": s.enabled,
                        "deleted": s.deleted,
                        "tools": _tools_json(s.tools),
                        "risk_overrides": json.dumps(s.risk_overrides),
                        "last_sync_at": s.last_sync_at,
                        "last_error": s.last_error,
                    },
                )
            ).first()
        if row is None:
            raise NotFoundError("Không tìm thấy MCP server.")
        return _row(row._mapping)
