from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from aiagent.modules.agents.domain.spec import AgentSpec


class AgentStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class CreatedVia(StrEnum):
    FORM = "form"
    API = "api"
    ARCHITECT = "architect"
    CLI = "cli"


@dataclass(frozen=True)
class Agent:
    id: UUID
    tenant_id: str
    name: str
    description: str
    owner_id: str
    status: AgentStatus
    latest_version: int
    published_version: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AgentVersion:
    agent_id: UUID
    version: int
    spec: AgentSpec
    created_by: str
    created_via: CreatedVia
    change_note: str
    created_at: datetime
