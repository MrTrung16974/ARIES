from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.identity.domain import Principal

router = APIRouter(prefix="/api/v1", tags=["identity"])


class MeResponse(BaseModel):
    subject: str
    tenant_id: str
    username: str | None
    email: str | None
    roles: list[str]
    created_at: datetime
    last_seen_at: datetime


@router.get("/me", response_model=MeResponse)
async def me(
    principal: Annotated[Principal, Depends(get_principal)],
    container: ContainerDep,
) -> MeResponse:
    profile = await container.identity.current_user(principal)
    return MeResponse(
        subject=profile.subject,
        tenant_id=profile.tenant_id,
        username=profile.username,
        email=profile.email,
        roles=sorted(r.value for r in principal.roles),
        created_at=profile.created_at,
        last_seen_at=profile.last_seen_at,
    )
