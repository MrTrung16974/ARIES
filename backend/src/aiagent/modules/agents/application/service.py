from __future__ import annotations

from uuid import UUID

from aiagent.modules.agents.domain import (
    Agent,
    AgentRepositoryPort,
    AgentSpec,
    AgentStatus,
    AgentVersion,
    ConnectionCatalogPort,
    CreatedVia,
    SkillCatalogPort,
)
from aiagent.modules.identity.domain import Principal, Role
from aiagent.shared_kernel.errors import ForbiddenError, NotFoundError, ValidationError

SYSTEM_OWNER = "system"
MASTER_NAME = "ARIES Master"

# Model được phép chọn. Mở rộng khi có model tự host (ADR-0012).
ALLOWED_MODELS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}),
    "fake": frozenset({"fake-echo"}),
}


class AgentService:
    def __init__(
        self,
        repo: AgentRepositoryPort,
        catalog: SkillCatalogPort,
        allowed_models: dict[str, frozenset[str]] = ALLOWED_MODELS,
        connections: ConnectionCatalogPort | None = None,
    ) -> None:
        self._repo = repo
        self._catalog = catalog
        self._allowed_models = allowed_models
        self._connections = connections

    # ----------------------------------------------------------- validation
    def validate(self, spec: AgentSpec) -> None:
        """Kiểm tham chiếu ngoài schema: model được phép, skill/tool tồn tại, tính năng chưa có."""
        errors: list[str] = []
        allowed = self._allowed_models.get(spec.model.provider, frozenset())
        if spec.model.model not in allowed:
            errors.append(f"Model '{spec.model.model}' không được phép cho '{spec.model.provider}'")
        for binding in spec.skills:
            tools = self._catalog.tools_of(binding.skill_id)
            if tools is None:
                errors.append(f"Skill '{binding.skill_id}' không tồn tại hoặc đang bị tắt")
                continue
            names = {t.name for t in tools}
            unknown = sorted(set(binding.allowed_tools) - names)
            if unknown:
                errors.append(f"Skill '{binding.skill_id}' không có tool: {', '.join(unknown)}")
        if spec.knowledge_bases:
            errors.append("Knowledge base chưa được hỗ trợ (Phase 4)")
        if errors:
            raise ValidationError("; ".join(errors))

    async def check_connections(
        self, principal: Principal, spec: AgentSpec, *, already: frozenset[str] = frozenset()
    ) -> None:
        """Người lưu spec phải được dùng mọi connection MỚI gắn vào (ADR-0016).

        Connection đã có trong version trước (`already`) được giữ nguyên — quyền dùng thực tế
        còn được kiểm lại lúc chạy theo người chạy.
        """
        errors: list[str] = []
        for binding in spec.connections:
            cid = str(binding.connection_id)
            if cid in already:
                continue
            tools = (
                await self._connections.tools_for(principal, cid)
                if self._connections is not None
                else None
            )
            if tools is None:
                errors.append(f"Kết nối {cid} không tồn tại hoặc bạn không được dùng")
                continue
            unknown = sorted(set(binding.allowed_tools) - {t.name for t in tools})
            if unknown:
                errors.append(f"Kết nối {cid} không có tool: {', '.join(unknown)}")
        if errors:
            raise ValidationError("; ".join(errors))

    async def check_team(self, principal: Principal, spec: AgentSpec) -> None:
        """Thành viên team fixed phải tồn tại, không phải supervisor (độ sâu 1)."""
        if spec.graph_type != "supervisor" or spec.team is None:
            return
        errors: list[str] = []
        for m in spec.team.members:
            agent = await self._repo.get(principal.tenant_id, m.agent_id)
            if agent is None or not self._visible(principal, agent):
                errors.append(f"Agent thành viên {m.agent_id} không tồn tại")
                continue
            number = agent.published_version or agent.latest_version
            version = await self._repo.get_version(principal.tenant_id, agent.id, number)
            if version is not None and version.spec.graph_type == "supervisor":
                errors.append(f"“{agent.name}” là agent điều phối — không thể làm thành viên")
        if errors:
            raise ValidationError("; ".join(errors))

    async def ensure_master(self, principal: Principal, *, provider: str, model: str) -> Agent:
        """ARIES Master của tenant (ADR-0017): tạo + publish lần đầu, chủ sở hữu là hệ thống."""
        for agent in await self._repo.list_all(principal.tenant_id):
            if agent.owner_id == SYSTEM_OWNER and agent.name == MASTER_NAME:
                return agent
        spec = AgentSpec.model_validate(
            {
                "name": MASTER_NAME,
                "description": "Đầu mối chung: hiểu yêu cầu và giao việc cho agent phù hợp.",
                "graph_type": "supervisor",
                "model": {"provider": provider, "model": model, "effort": "medium"},
                "persona": "Bạn là ARIES Master — trợ lý điều phối của tổ chức. Bạn nhận yêu cầu "
                "của người dùng, chia nhỏ khi cần và giao cho các agent chuyên trách.",
                "opening_statement": "Chào bạn! Cứ nói việc cần làm, mình sẽ giao cho agent phù hợp.",
                "team": {"mode": "auto"},
                "data_classification": "internal",
            }
        )
        agent, version = await self._repo.create(
            principal.tenant_id, SYSTEM_OWNER, spec, CreatedVia.API, "Tạo tự động"
        )
        return await self._repo.set_published(principal.tenant_id, agent.id, version.version)

    # ----------------------------------------------------------- permissions
    @staticmethod
    def _require_builder(principal: Principal) -> None:
        if not principal.has_any_role(Role.BUILDER, Role.ADMIN):
            raise ForbiddenError()

    @staticmethod
    def can_edit(principal: Principal, agent: Agent) -> bool:
        return principal.has_any_role(Role.ADMIN) or (
            principal.has_any_role(Role.BUILDER) and agent.owner_id == principal.subject
        )

    async def _editable(self, principal: Principal, agent_id: UUID) -> Agent:
        agent = await self.get(principal, agent_id)
        if not self.can_edit(principal, agent):
            raise ForbiddenError()
        return agent

    # ----------------------------------------------------------- commands
    async def create(
        self,
        principal: Principal,
        spec: AgentSpec,
        *,
        via: CreatedVia = CreatedVia.API,
        note: str = "",
    ) -> tuple[Agent, AgentVersion]:
        self._require_builder(principal)
        self.validate(spec)
        await self.check_connections(principal, spec)
        await self.check_team(principal, spec)
        return await self._repo.create(principal.tenant_id, principal.subject, spec, via, note)

    async def update(
        self,
        principal: Principal,
        agent_id: UUID,
        spec: AgentSpec,
        *,
        expected_version: int | None,
        via: CreatedVia = CreatedVia.API,
        note: str = "",
    ) -> AgentVersion:
        agent = await self._editable(principal, agent_id)
        self.validate(spec)
        latest = await self._repo.get_version(principal.tenant_id, agent_id, agent.latest_version)
        already = frozenset(
            str(c.connection_id) for c in (latest.spec.connections if latest else [])
        )
        await self.check_connections(principal, spec, already=already)
        await self.check_team(principal, spec)
        return await self._repo.add_version(
            principal.tenant_id, agent_id, spec, principal.subject, via, note, expected_version
        )

    async def publish(self, principal: Principal, agent_id: UUID, version: int) -> Agent:
        await self._editable(principal, agent_id)
        found = await self._repo.get_version(principal.tenant_id, agent_id, version)
        if found is None:
            raise NotFoundError("Không tìm thấy version.")
        self.validate(found.spec)  # skill có thể đã bị gỡ kể từ lúc tạo version
        return await self._repo.set_published(principal.tenant_id, agent_id, version)

    async def unpublish(self, principal: Principal, agent_id: UUID) -> Agent:
        await self._editable(principal, agent_id)
        return await self._repo.set_published(principal.tenant_id, agent_id, None)

    async def archive(self, principal: Principal, agent_id: UUID) -> Agent:
        await self._editable(principal, agent_id)
        return await self._repo.set_archived(principal.tenant_id, agent_id, True)

    # ----------------------------------------------------------- queries
    async def get(self, principal: Principal, agent_id: UUID) -> Agent:
        agent = await self._repo.get(principal.tenant_id, agent_id)
        if agent is None or not self._visible(principal, agent):
            raise NotFoundError("Không tìm thấy agent.")
        return agent

    async def list_agents(self, principal: Principal) -> list[Agent]:
        agents = await self._repo.list_all(principal.tenant_id)
        return [a for a in agents if self._visible(principal, a)]

    async def versions(self, principal: Principal, agent_id: UUID) -> list[AgentVersion]:
        await self._editable(principal, agent_id)
        return await self._repo.list_versions(principal.tenant_id, agent_id)

    async def version(self, principal: Principal, agent_id: UUID, version: int) -> AgentVersion:
        agent = await self.get(principal, agent_id)
        if version != agent.published_version and not self.can_edit(principal, agent):
            raise NotFoundError("Không tìm thấy version.")
        found = await self._repo.get_version(principal.tenant_id, agent_id, version)
        if found is None:
            raise NotFoundError("Không tìm thấy version.")
        return found

    async def resolve_for_run(
        self, principal: Principal, agent_id: UUID, *, draft: bool
    ) -> tuple[Agent, AgentVersion]:
        """Version để chạy: bản mới nhất (draft, chỉ người sửa được) hoặc bản đã publish."""
        agent = await self.get(principal, agent_id)
        if draft:
            if not self.can_edit(principal, agent):
                raise ForbiddenError()
            number = agent.latest_version
        else:
            if agent.published_version is None:
                raise ValidationError("Agent chưa được publish.")
            number = agent.published_version
        found = await self._repo.get_version(principal.tenant_id, agent_id, number)
        if found is None:
            raise NotFoundError("Không tìm thấy version.")
        return agent, found

    def _visible(self, principal: Principal, agent: Agent) -> bool:
        if agent.status is AgentStatus.ARCHIVED:
            return self.can_edit(principal, agent)
        return agent.published_version is not None or self.can_edit(principal, agent)
