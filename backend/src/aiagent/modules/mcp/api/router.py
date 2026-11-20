"""API MCP server (ADR-0018). Secret (header/biến môi trường) chỉ ghi vào, không bao giờ trả ra."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.identity.domain import Principal
from aiagent.modules.mcp.domain import SLUG_PATTERN, McpServer, McpTransport, tool_manifest_name

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])

AnyUser = Annotated[Principal, Depends(get_principal)]


class McpToolOut(BaseModel):
    name: str  # tên gốc trên server
    tool: str  # tên dùng trong agent (mcp.<slug>.<tool>)
    description: str
    risk: str
    detected_risk: str


class McpServerOut(BaseModel):
    id: UUID
    name: str
    slug: str
    transport: McpTransport
    url: str | None
    command_key: str | None
    secret_keys: list[str]
    enabled: bool
    tools: list[McpToolOut]
    last_sync_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class CreateMcpServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(pattern=SLUG_PATTERN)
    transport: McpTransport
    url: str | None = Field(default=None, max_length=2000)
    command_key: str | None = Field(default=None, max_length=64)
    secrets: dict[str, str] = Field(default_factory=dict, repr=False)


class UpdateMcpServerIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    secrets: dict[str, str] | None = Field(default=None, repr=False)
    risk_overrides: dict[str, str] | None = None


def _out(s: McpServer) -> McpServerOut:
    return McpServerOut(
        id=s.id,
        name=s.name,
        slug=s.slug,
        transport=s.transport,
        url=s.url,
        command_key=s.command_key,
        secret_keys=s.secret_keys,
        enabled=s.enabled,
        tools=[
            McpToolOut(
                name=t.name,
                tool=tool_manifest_name(s.slug, t.name),
                description=t.description,
                risk=s.effective_risk(t).value,
                detected_risk=t.risk.value,
            )
            for t in s.tools
        ],
        last_sync_at=s.last_sync_at,
        last_error=s.last_error,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.get("/commands", response_model=list[str])
async def stdio_commands(p: AnyUser, c: ContainerDep) -> list[str]:
    """Các lệnh stdio người vận hành đã cho phép (chỉ hiện tên, không hiện dòng lệnh)."""
    c.mcp.require_admin(p)
    return c.mcp.stdio_commands()


@router.get("/servers", response_model=list[McpServerOut])
async def list_servers(p: AnyUser, c: ContainerDep) -> list[McpServerOut]:
    return [_out(s) for s in await c.mcp.list_servers(p)]


@router.post("/servers", response_model=McpServerOut, status_code=status.HTTP_201_CREATED)
async def create_server(body: CreateMcpServerIn, p: AnyUser, c: ContainerDep) -> McpServerOut:
    s = await c.mcp.create(
        p,
        name=body.name,
        slug=body.slug,
        transport=body.transport,
        url=body.url,
        command_key=body.command_key,
        secrets=body.secrets,
    )
    return _out(s)


@router.patch("/servers/{server_id}", response_model=McpServerOut)
async def update_server(
    server_id: UUID, body: UpdateMcpServerIn, p: AnyUser, c: ContainerDep
) -> McpServerOut:
    kwargs: dict[str, Any] = body.model_dump(exclude_none=True)
    return _out(await c.mcp.update(p, server_id, **kwargs))


@router.post("/servers/{server_id}/sync", response_model=McpServerOut)
async def sync_server(server_id: UUID, p: AnyUser, c: ContainerDep) -> McpServerOut:
    return _out(await c.mcp.sync(p, server_id))


@router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(server_id: UUID, p: AnyUser, c: ContainerDep) -> Response:
    await c.mcp.delete(p, server_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
