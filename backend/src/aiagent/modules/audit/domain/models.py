from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from aiagent.shared_kernel.clock import utcnow


class Outcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


@dataclass(frozen=True, kw_only=True)
class AuditEvent:
    tenant_id: str
    actor_id: str | None
    action: str
    outcome: Outcome
    resource_type: str | None = None
    resource_id: str | None = None
    ip: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=utcnow)


@dataclass(frozen=True)
class AuditRecord:
    seq: int
    event: AuditEvent
    prev_hash: str
    hash: str
