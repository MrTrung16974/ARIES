from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from aiagent.adapters.db import Database
from aiagent.modules.agents.domain import (
    Agent,
    AgentSpec,
    AgentStatus,
    AgentVersion,
    CreatedVia,
)
from aiagent.shared_kernel.errors import ConflictError, NotFoundError

_INSERT_AGENT = text(
    """
    INSERT INTO agents.agents (id, tenant_id, name, description, owner_id, status, latest_version)
    VALUES (:id, :tenant_id, :name, :description, :owner_id, 'draft', 1)
    RETURNING id, tenant_id, name, description, owner_id, status, latest_version,
              published_version, created_at, updated_at
    """
)
_INSERT_VERSION = text(
    """
    INSERT INTO agents.agent_versions
      (agent_id, tenant_id, version, spec, created_by, created_via, change_note)
    VALUES (:agent_id, :tenant_id, :version, CAST(:spec AS jsonb), :created_by, :created_via, :note)
    RETURNING agent_id, version, spec, created_by, created_via, change_note, created_at
    """
)
_BUMP_VERSION = text(
    """
    UPDATE agents.agents
       SET latest_version = latest_version + 1, name = :name, description = :description,
           updated_at = now()
     WHERE id = :id
       AND (CAST(:expected AS integer) IS NULL OR latest_version = CAST(:expected AS integer))
    RETURNING latest_version
    """
)
_EXISTS = text("SELECT latest_version FROM agents.agents WHERE id = :id")
_GET = text(
    """
    SELECT id, tenant_id, name, description, owner_id, status, latest_version, published_version,
           created_at, updated_at
      FROM agents.agents WHERE id = :id
    """
)
_LIST = text(
    """
    SELECT id, tenant_id, name, description, owner_id, status, latest_version, published_version,
           created_at, updated_at
      FROM agents.agents
     WHERE (:include_archived OR status <> 'archived')
     ORDER BY updated_at DESC
    """
)
_GET_VERSION = text(
    """
    SELECT agent_id, version, spec, created_by, created_via, change_note, created_at
      FROM agents.agent_versions WHERE agent_id = :agent_id AND version = :version
    """
)
_LIST_VERSIONS = text(
    """
    SELECT agent_id, version, spec, created_by, created_via, change_note, created_at
      FROM agents.agent_versions WHERE agent_id = :agent_id ORDER BY version DESC
    """
)
_SET_PUBLISHED = text(
    """
    UPDATE agents.agents
       SET published_version = CAST(:version AS integer),
           status = CASE WHEN CAST(:version AS integer) IS NULL THEN 'draft' ELSE 'published' END,
           updated_at = now()
     WHERE id = :id
    RETURNING id, tenant_id, name, description, owner_id, status, latest_version,
              published_version, created_at, updated_at
    """
)
_SET_ARCHIVED = text(
    """
    UPDATE agents.agents
       SET status = CASE WHEN :archived THEN 'archived'
                         WHEN published_version IS NULL THEN 'draft' ELSE 'published' END,
           updated_at = now()
     WHERE id = :id
    RETURNING id, tenant_id, name, description, owner_id, status, latest_version,
              published_version, created_at, updated_at
    """
)


def _dump(spec: AgentSpec) -> str:
    return json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


class SqlAgentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self, tenant_id: str, owner_id: str, spec: AgentSpec, via: CreatedVia, note: str
    ) -> tuple[Agent, AgentVersion]:
        agent_id = uuid4()
        async with self._db.tenant_session(tenant_id) as s:
            row = (
                await s.execute(
                    _INSERT_AGENT,
                    {
                        "id": agent_id,
                        "tenant_id": tenant_id,
                        "name": spec.name,
                        "description": spec.description,
                        "owner_id": owner_id,
                    },
                )
            ).one()
            vrow = (
                await s.execute(
                    _INSERT_VERSION,
                    {
                        "agent_id": agent_id,
                        "tenant_id": tenant_id,
                        "version": 1,
                        "spec": _dump(spec),
                        "created_by": owner_id,
                        "created_via": via.value,
                        "note": note,
                    },
                )
            ).one()
        return _agent(row._mapping), _version(vrow._mapping)

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
        async with self._db.tenant_session(tenant_id) as s:
            bumped = (
                await s.execute(
                    _BUMP_VERSION,
                    {
                        "id": agent_id,
                        "expected": expected_latest,
                        "name": spec.name,
                        "description": spec.description,
                    },
                )
            ).scalar()
            if bumped is None:
                exists = (await s.execute(_EXISTS, {"id": agent_id})).scalar()
                if exists is None:
                    raise NotFoundError("Không tìm thấy agent.")
                raise ConflictError(
                    f"Agent đã được sửa ở nơi khác (version {exists}). Tải lại rồi thử lại."
                )
            vrow = (
                await s.execute(
                    _INSERT_VERSION,
                    {
                        "agent_id": agent_id,
                        "tenant_id": tenant_id,
                        "version": bumped,
                        "spec": _dump(spec),
                        "created_by": created_by,
                        "created_via": via.value,
                        "note": note,
                    },
                )
            ).one()
        return _version(vrow._mapping)

    async def get(self, tenant_id: str, agent_id: UUID) -> Agent | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET, {"id": agent_id})).first()
        return _agent(row._mapping) if row else None

    async def list_all(self, tenant_id: str, *, include_archived: bool = False) -> list[Agent]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST, {"include_archived": include_archived})).all()
        return [_agent(r._mapping) for r in rows]

    async def get_version(
        self, tenant_id: str, agent_id: UUID, version: int
    ) -> AgentVersion | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (
                await s.execute(_GET_VERSION, {"agent_id": agent_id, "version": version})
            ).first()
        return _version(row._mapping) if row else None

    async def list_versions(self, tenant_id: str, agent_id: UUID) -> list[AgentVersion]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST_VERSIONS, {"agent_id": agent_id})).all()
        return [_version(r._mapping) for r in rows]

    async def set_published(self, tenant_id: str, agent_id: UUID, version: int | None) -> Agent:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_SET_PUBLISHED, {"id": agent_id, "version": version})).first()
        if row is None:
            raise NotFoundError("Không tìm thấy agent.")
        return _agent(row._mapping)

    async def set_archived(self, tenant_id: str, agent_id: UUID, archived: bool) -> Agent:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_SET_ARCHIVED, {"id": agent_id, "archived": archived})).first()
        if row is None:
            raise NotFoundError("Không tìm thấy agent.")
        return _agent(row._mapping)


def _agent(m: Any) -> Agent:
    return Agent(
        id=m["id"],
        tenant_id=m["tenant_id"],
        name=m["name"],
        description=m["description"],
        owner_id=m["owner_id"],
        status=AgentStatus(m["status"]),
        latest_version=m["latest_version"],
        published_version=m["published_version"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def _version(m: Any) -> AgentVersion:
    spec = m["spec"]
    if isinstance(spec, str):
        spec = json.loads(spec)
    return AgentVersion(
        agent_id=m["agent_id"],
        version=m["version"],
        spec=AgentSpec.model_validate(spec),
        created_by=m["created_by"],
        created_via=CreatedVia(m["created_via"]),
        change_note=m["change_note"],
        created_at=m["created_at"],
    )
