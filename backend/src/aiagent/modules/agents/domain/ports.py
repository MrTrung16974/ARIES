from __future__ import annotations

from typing import Protocol
from uuid import UUID

from aiagent.modules.agents.domain.models import Agent, AgentVersion, CreatedVia
from aiagent.modules.agents.domain.spec import AgentSpec
from aiagent.plugin_sdk import ToolSpec


class AgentRepositoryPort(Protocol):
    async def create(
        self, tenant_id: str, owner_id: str, spec: AgentSpec, via: CreatedVia, note: str
    ) -> tuple[Agent, AgentVersion]: ...

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
        """Thêm version mới. `expected_latest` dùng cho optimistic locking (ConflictError)."""
        ...

    async def get(self, tenant_id: str, agent_id: UUID) -> Agent | None: ...

    async def list_all(self, tenant_id: str, *, include_archived: bool = False) -> list[Agent]: ...

    async def get_version(
        self, tenant_id: str, agent_id: UUID, version: int
    ) -> AgentVersion | None: ...

    async def list_versions(self, tenant_id: str, agent_id: UUID) -> list[AgentVersion]: ...

    async def set_published(self, tenant_id: str, agent_id: UUID, version: int | None) -> Agent: ...

    async def set_archived(self, tenant_id: str, agent_id: UUID, archived: bool) -> Agent: ...


class SkillCatalogPort(Protocol):
    def tools_of(self, skill_id: str) -> list[ToolSpec] | None:
        """Danh sách tool của skill đang hoạt động; None nếu skill không tồn tại/bị tắt."""
        ...
