from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aiagent import __version__
from aiagent.api.deps import ContainerDep, require_roles
from aiagent.modules.identity.domain import Principal, Role

router = APIRouter(tags=["system"])


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness: tiến trình còn sống."""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(container: ContainerDep) -> JSONResponse:
    """Readiness: các phụ thuộc (DB, Redis) sẵn sàng."""
    checks = await container.readiness()
    ok = all(checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "checks": checks},
        status_code=200 if ok else 503,
    )


class SystemInfo(BaseModel):
    name: str
    version: str
    env: str


@router.get("/api/v1/system/info", response_model=SystemInfo)
async def info(container: ContainerDep) -> SystemInfo:
    s = container.settings
    return SystemInfo(name=s.app_name, version=__version__, env=s.env)


class PluginOut(BaseModel):
    entry_point: str
    status: str
    reason: str
    id: str | None
    kind: str | None
    name: str | None
    version: str | None
    tools: list[dict[str, Any]]


@router.get("/api/v1/system/plugins", response_model=list[PluginOut])
async def plugins(
    _: Annotated[Principal, Depends(require_roles(Role.ADMIN))],
    container: ContainerDep,
) -> list[PluginOut]:
    out: list[PluginOut] = []
    for p in container.plugins.all():
        m = p.manifest
        out.append(
            PluginOut(
                entry_point=p.entry_point,
                status=p.status.value,
                reason=p.reason,
                id=m.id if m else None,
                kind=m.kind.value if m else None,
                name=m.name if m else None,
                version=m.version if m else None,
                tools=[t.model_dump(mode="json") for t in m.tools] if m else [],
            )
        )
    return out
