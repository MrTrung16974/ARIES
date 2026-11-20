"""API connector + connection. Secret chỉ đi vào (write-only), không bao giờ trả ra."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.connections.domain import Connection, ConnectionScope
from aiagent.modules.identity.domain import Principal

router = APIRouter(prefix="/api/v1", tags=["connections"])

AnyUser = Annotated[Principal, Depends(get_principal)]


class ConnectorToolOut(BaseModel):
    name: str
    description: str
    risk: str


class ConnectorOut(BaseModel):
    id: str
    name: str
    description: str
    version: str
    config_schema: dict[str, Any]
    secrets: list[str]
    tools: list[ConnectorToolOut]
    ui_hints: dict[str, Any]


class ConnectionOut(BaseModel):
    id: UUID
    name: str
    connector_id: str
    scope: ConnectionScope
    owner_id: str
    config: dict[str, Any]
    secret_keys: list[str]
    status: str
    last_test_ok: bool | None
    last_test_note: str | None
    last_test_at: datetime | None
    created_at: datetime
    updated_at: datetime
    can_manage: bool
    can_use: bool


class CreateConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    connector_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    scope: ConnectionScope = ConnectionScope.PERSONAL
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict, repr=False)


class UpdateConnectionIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    config: dict[str, Any] | None = None
    secrets: dict[str, str] | None = Field(default=None, repr=False)
    enabled: bool | None = None


def _out(c: Any, p: Principal, conn: Connection) -> ConnectionOut:
    svc = c.connections
    return ConnectionOut(
        id=conn.id,
        name=conn.name,
        connector_id=conn.connector_id,
        scope=conn.scope,
        owner_id=conn.owner_id,
        config=conn.config,
        secret_keys=conn.secret_keys,
        status=conn.status.value,
        last_test_ok=conn.last_test_ok,
        last_test_note=conn.last_test_note,
        last_test_at=conn.last_test_at,
        created_at=conn.created_at,
        updated_at=conn.updated_at,
        can_manage=svc.can_manage(p, conn),
        can_use=svc.can_use(p, conn),
    )


@router.get("/connectors", response_model=list[ConnectorOut])
async def list_connectors(_: AnyUser, c: ContainerDep) -> list[ConnectorOut]:
    return [
        ConnectorOut(
            id=m.id,
            name=m.name,
            description=m.description,
            version=m.version,
            config_schema=m.config_schema,
            secrets=list(m.secrets),
            tools=[
                ConnectorToolOut(name=t.name, description=t.description, risk=t.risk.value)
                for t in m.tools
            ],
            ui_hints=m.ui_hints,
        )
        for m in c.connections.connectors()
    ]


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(p: AnyUser, c: ContainerDep) -> list[ConnectionOut]:
    return [_out(c, p, x) for x in await c.connections.list_visible(p)]


@router.post("/connections", response_model=ConnectionOut, status_code=status.HTTP_201_CREATED)
async def create_connection(body: CreateConnectionIn, p: AnyUser, c: ContainerDep) -> ConnectionOut:
    conn = await c.connections.create(
        p,
        name=body.name,
        connector_id=body.connector_id,
        scope=body.scope,
        config=body.config,
        secrets=body.secrets,
    )
    return _out(c, p, conn)


@router.get("/connections/{connection_id}", response_model=ConnectionOut)
async def get_connection(connection_id: UUID, p: AnyUser, c: ContainerDep) -> ConnectionOut:
    return _out(c, p, await c.connections.get(p, connection_id))


@router.patch("/connections/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: UUID, body: UpdateConnectionIn, p: AnyUser, c: ContainerDep
) -> ConnectionOut:
    conn = await c.connections.update(
        p,
        connection_id,
        name=body.name,
        config=body.config,
        secrets=body.secrets,
        enabled=body.enabled,
    )
    return _out(c, p, conn)


@router.post("/connections/{connection_id}/test", response_model=ConnectionOut)
async def test_connection(connection_id: UUID, p: AnyUser, c: ContainerDep) -> ConnectionOut:
    return _out(c, p, await c.connections.test(p, connection_id))


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(connection_id: UUID, p: AnyUser, c: ContainerDep) -> Response:
    await c.connections.delete(p, connection_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
