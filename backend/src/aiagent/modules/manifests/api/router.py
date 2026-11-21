"""API agent-as-code (ADR-0022): export/apply manifest, thư viện mẫu, cấu hình CLI."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.identity.domain import Principal
from aiagent.modules.manifests.application import ApplyResult
from aiagent.modules.manifests.domain import MAX_MANIFEST_BYTES

router = APIRouter(prefix="/api/v1", tags=["agent-as-code"])

AnyUser = Annotated[Principal, Depends(get_principal)]


class ApplyIn(BaseModel):
    manifest: str = Field(min_length=1, max_length=MAX_MANIFEST_BYTES)
    dry_run: bool = False
    publish: bool = False


class ApplyOut(BaseModel):
    action: str
    agent_id: UUID | None
    version: int | None
    changed: list[str]
    published: bool
    dry_run: bool


class TemplateOut(BaseModel):
    id: str
    title: str
    description: str
    category: str
    skills: list[str]
    data_classification: str


class CliConfigOut(BaseModel):
    issuer: str
    client_id: str
    api_version: str = "v1"


def _apply_out(r: ApplyResult) -> ApplyOut:
    return ApplyOut(
        action=r.action,
        agent_id=r.agent_id,
        version=r.version,
        changed=r.changed,
        published=r.published,
        dry_run=r.dry_run,
    )


@router.get("/agents/{agent_id}/export")
async def export_agent(
    agent_id: UUID,
    principal: AnyUser,
    c: ContainerDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> Response:
    text = await c.manifests.export(principal, agent_id, version)
    return Response(
        content=text,
        media_type="application/yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="agent-{agent_id}.yaml"'},
    )


@router.post("/agents/apply", response_model=ApplyOut)
async def apply_manifest(body: ApplyIn, principal: AnyUser, c: ContainerDep) -> ApplyOut:
    result = await c.manifests.apply(
        principal, body.manifest, dry_run=body.dry_run, publish=body.publish
    )
    return _apply_out(result)


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(principal: AnyUser, c: ContainerDep) -> list[TemplateOut]:
    out = []
    for t in c.manifests.templates():
        spec: dict[str, Any] = t.manifest["spec"]
        out.append(
            TemplateOut(
                id=t.id,
                title=t.title,
                description=t.description,
                category=t.category,
                skills=[s.get("skill_id", "") for s in spec.get("skills") or []],
                data_classification=str(spec.get("data_classification", "internal")),
            )
        )
    return out


@router.post("/templates/{template_id}/use", response_model=ApplyOut, status_code=201)
async def use_template(template_id: str, principal: AnyUser, c: ContainerDep) -> ApplyOut:
    return _apply_out(await c.manifests.use_template(principal, template_id))


@router.get("/cli/config", response_model=CliConfigOut)
async def cli_config(c: ContainerDep) -> CliConfigOut:
    """Công khai: CLI biết đăng nhập ở đâu (issuer + client công khai, không có bí mật)."""
    return CliConfigOut(issuer=c.settings.oidc_issuer, client_id=c.settings.cli_client_id)
