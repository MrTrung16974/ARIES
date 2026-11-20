from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, client_ip, get_principal, require_roles
from aiagent.bootstrap.container import Container
from aiagent.modules.agents.domain import Agent, AgentSpec, AgentVersion, CreatedVia
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.identity.domain import Principal, Role

router = APIRouter(prefix="/api/v1", tags=["agents"])

AnyUser = Annotated[Principal, Depends(get_principal)]
Builder = Annotated[Principal, Depends(require_roles(Role.BUILDER, Role.ADMIN))]


class AgentOut(BaseModel):
    id: UUID
    name: str
    description: str
    status: str
    owner_id: str
    latest_version: int
    published_version: int | None
    can_edit: bool
    created_at: datetime
    updated_at: datetime


class VersionOut(BaseModel):
    version: int
    spec: AgentSpec
    created_by: str
    created_via: str
    change_note: str
    created_at: datetime


class AgentDetailOut(AgentOut):
    current: VersionOut


class CreateAgentIn(BaseModel):
    spec: AgentSpec
    note: str = Field(default="", max_length=500)
    via: CreatedVia = CreatedVia.FORM


class UpdateAgentIn(CreateAgentIn):
    expected_version: int | None = Field(default=None, ge=1)


class PublishIn(BaseModel):
    version: int = Field(ge=1)


class ToolOut(BaseModel):
    name: str
    description: str
    risk: str
    input_schema: dict[str, Any]


class SkillOut(BaseModel):
    id: str
    name: str
    description: str
    version: str
    tools: list[ToolOut]
    ui_hints: dict[str, Any]


def _agent_out(c: Container, p: Principal, a: Agent) -> AgentOut:
    return AgentOut(
        id=a.id,
        name=a.name,
        description=a.description,
        status=a.status.value,
        owner_id=a.owner_id,
        latest_version=a.latest_version,
        published_version=a.published_version,
        can_edit=c.agents.can_edit(p, a),
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


def _version_out(v: AgentVersion) -> VersionOut:
    return VersionOut(
        version=v.version,
        spec=v.spec,
        created_by=v.created_by,
        created_via=v.created_via.value,
        change_note=v.change_note,
        created_at=v.created_at,
    )


async def _audit(
    c: Container, p: Principal, req: Request, action: str, agent_id: UUID, details: dict[str, Any]
) -> None:
    await c.audit.record_safely(
        tenant_id=p.tenant_id,
        actor_id=p.subject,
        action=action,
        outcome=Outcome.SUCCESS,
        resource_type="agent",
        resource_id=str(agent_id),
        ip=client_ip(req),
        details=details,
    )


# ------------------------------------------------------------------ catalog
@router.get("/agents/schema", response_model=dict[str, Any])
async def agent_schema(_: AnyUser) -> dict[str, Any]:
    """JSON Schema của AgentSpec — dùng cho form nâng cao và Agent Architect."""
    return AgentSpec.model_json_schema()


@router.get("/catalog/skills", response_model=list[SkillOut])
async def list_skills(_: Builder, c: ContainerDep) -> list[SkillOut]:
    out: list[SkillOut] = []
    for loaded in c.plugins.active():
        m = loaded.manifest
        if m is None or m.kind.value != "skill":
            continue
        out.append(
            SkillOut(
                id=m.id,
                name=m.name,
                description=m.description,
                version=m.version,
                tools=[ToolOut(**t.model_dump(mode="json")) for t in m.tools],
                ui_hints=m.ui_hints,
            )
        )
    return out


class ModelOut(BaseModel):
    provider: str
    model: str
    available: bool


@router.get("/catalog/models", response_model=list[ModelOut])
async def list_models(_: Builder, c: ContainerDep) -> list[ModelOut]:
    return [
        ModelOut(provider=e.provider, model=e.model, available=e.available)
        for e in await c.model_catalog.entries()
        if e.available or e.provider != "fake"
    ]


# ------------------------------------------------------------------ agents
@router.get("/agents", response_model=list[AgentOut])
async def list_agents(p: AnyUser, c: ContainerDep) -> list[AgentOut]:
    return [_agent_out(c, p, a) for a in await c.agents.list_agents(p)]


@router.post("/agents", response_model=AgentDetailOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: CreateAgentIn, p: Builder, c: ContainerDep, req: Request
) -> AgentDetailOut:
    agent, version = await c.agents.create(p, body.spec, via=body.via, note=body.note)
    await _audit(c, p, req, "agent.created", agent.id, {"version": 1, "via": body.via.value})
    return AgentDetailOut(**_agent_out(c, p, agent).model_dump(), current=_version_out(version))


@router.get("/agents/{agent_id}", response_model=AgentDetailOut)
async def get_agent(agent_id: UUID, p: AnyUser, c: ContainerDep) -> AgentDetailOut:
    agent = await c.agents.get(p, agent_id)
    number = agent.latest_version if c.agents.can_edit(p, agent) else agent.published_version
    version = await c.agents.version(p, agent_id, number or agent.latest_version)
    return AgentDetailOut(**_agent_out(c, p, agent).model_dump(), current=_version_out(version))


@router.put("/agents/{agent_id}", response_model=VersionOut)
async def update_agent(
    agent_id: UUID, body: UpdateAgentIn, p: Builder, c: ContainerDep, req: Request
) -> VersionOut:
    version = await c.agents.update(
        p, agent_id, body.spec, expected_version=body.expected_version, via=body.via, note=body.note
    )
    await _audit(c, p, req, "agent.updated", agent_id, {"version": version.version})
    return _version_out(version)


@router.get("/agents/{agent_id}/versions", response_model=list[VersionOut])
async def list_versions(agent_id: UUID, p: Builder, c: ContainerDep) -> list[VersionOut]:
    return [_version_out(v) for v in await c.agents.versions(p, agent_id)]


@router.get("/agents/{agent_id}/versions/{version}", response_model=VersionOut)
async def get_version(agent_id: UUID, version: int, p: AnyUser, c: ContainerDep) -> VersionOut:
    return _version_out(await c.agents.version(p, agent_id, version))


@router.post("/agents/{agent_id}/publish", response_model=AgentOut)
async def publish(
    agent_id: UUID, body: PublishIn, p: Builder, c: ContainerDep, req: Request
) -> AgentOut:
    agent = await c.agents.publish(p, agent_id, body.version)
    await _audit(c, p, req, "agent.published", agent_id, {"version": body.version})
    return _agent_out(c, p, agent)


@router.post("/agents/{agent_id}/unpublish", response_model=AgentOut)
async def unpublish(agent_id: UUID, p: Builder, c: ContainerDep, req: Request) -> AgentOut:
    agent = await c.agents.unpublish(p, agent_id)
    await _audit(c, p, req, "agent.unpublished", agent_id, {})
    return _agent_out(c, p, agent)


@router.delete("/agents/{agent_id}", response_model=AgentOut)
async def archive(agent_id: UUID, p: Builder, c: ContainerDep, req: Request) -> AgentOut:
    agent = await c.agents.archive(p, agent_id)
    await _audit(c, p, req, "agent.archived", agent_id, {})
    return _agent_out(c, p, agent)
