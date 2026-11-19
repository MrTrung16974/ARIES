from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from aiagent.api.deps import ContainerDep, require_roles
from aiagent.modules.identity.domain import Principal, Role

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

AuditReader = Annotated[Principal, Depends(require_roles(Role.AUDITOR))]


class AuditEventOut(BaseModel):
    seq: int
    event_id: str
    occurred_at: datetime
    actor_id: str | None
    action: str
    outcome: str
    resource_type: str | None
    resource_id: str | None
    ip: str | None
    details: dict[str, Any]


class ChainStatusOut(BaseModel):
    intact: bool
    records_checked: int
    broken_at_seq: int | None


@router.get("/events", response_model=list[AuditEventOut])
async def list_events(
    principal: AuditReader,
    container: ContainerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_seq: Annotated[int | None, Query(ge=1)] = None,
) -> list[AuditEventOut]:
    records = await container.audit.recent(principal.tenant_id, limit=limit, before_seq=before_seq)
    return [
        AuditEventOut(
            seq=r.seq,
            event_id=str(r.event.event_id),
            occurred_at=r.event.occurred_at,
            actor_id=r.event.actor_id,
            action=r.event.action,
            outcome=r.event.outcome.value,
            resource_type=r.event.resource_type,
            resource_id=r.event.resource_id,
            ip=r.event.ip,
            details=r.event.details,
        )
        for r in records
    ]


@router.get("/verify", response_model=ChainStatusOut)
async def verify_chain(principal: AuditReader, container: ContainerDep) -> ChainStatusOut:
    status = await container.audit.verify_chain(principal.tenant_id)
    return ChainStatusOut(
        intact=status.intact,
        records_checked=status.records_checked,
        broken_at_seq=status.broken_at_seq,
    )
