from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from aiagent.modules.builder.domain.models import (
    BuilderSession,
    CapabilityPlan,
    EditProposal,
    EvalCase,
    EvalVerdict,
    IntentResult,
    PromptDraft,
    RequirementBrief,
    SessionStatus,
)
from aiagent.shared_kernel.usage import Usage


@dataclass(frozen=True)
class CatalogTool:
    name: str
    description: str
    risk: str


@dataclass(frozen=True)
class CatalogSkill:
    id: str
    name: str
    description: str
    tools: list[CatalogTool]


@dataclass(frozen=True)
class CatalogConnection:
    """Connection người tạo ĐƯỢC DÙNG (personal của mình hoặc workspace)."""

    id: str
    name: str
    connector_id: str
    connector_name: str
    tools: list[CatalogTool]


@dataclass(frozen=True)
class Catalog:
    """Những gì người tạo ĐƯỢC PHÉP dùng — Architect chỉ chọn trong đây (chống bịa, chống leo thang)."""

    skills: list[CatalogSkill]
    models: list[str]  # model khả dụng, model đầu tiên là mặc định
    connections: list[CatalogConnection] = field(default_factory=list)

    def skill_ids(self) -> list[str]:
        return [s.id for s in self.skills]

    def tool_names(self) -> list[str]:
        return [t.name for s in self.skills for t in s.tools]

    def connection_ids(self) -> list[str]:
        return [c.id for c in self.connections]

    def connection_tool_names(self) -> list[str]:
        return sorted({t.name for c in self.connections for t in c.tools})


@dataclass
class UsageMeter:
    usage: Usage = field(default_factory=Usage)
    calls: int = 0

    def add(self, usage: Usage) -> None:
        self.usage = self.usage + usage
        self.calls += 1


class ArchitectBrainPort(Protocol):
    """Các bước cần "suy nghĩ" của Architect. Bản LLM (Claude) và bản heuristic (dev) cùng hiện thực."""

    @property
    def name(self) -> str: ...

    async def classify(self, message: str, has_draft: bool, meter: UsageMeter) -> IntentResult: ...

    async def understand(
        self, messages: list[str], allow_questions: bool, meter: UsageMeter
    ) -> RequirementBrief: ...

    async def plan(
        self, brief: RequirementBrief, catalog: Catalog, meter: UsageMeter
    ) -> CapabilityPlan: ...

    async def compose(
        self, brief: RequirementBrief, plan: CapabilityPlan, errors: list[str], meter: UsageMeter
    ) -> PromptDraft: ...

    async def edit(
        self, spec: dict[str, Any], instruction: str, catalog: Catalog, meter: UsageMeter
    ) -> EditProposal: ...

    async def generate_cases(
        self, brief: RequirementBrief | None, spec: dict[str, Any], meter: UsageMeter
    ) -> list[EvalCase]: ...

    async def judge(
        self,
        case: EvalCase,
        answer: str,
        tools_used: list[str],
        spec: dict[str, Any],
        meter: UsageMeter,
    ) -> EvalVerdict: ...


class BuilderSessionRepositoryPort(Protocol):
    async def create(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: UUID | None,
        base_version: int | None,
        draft_spec: dict[str, Any] | None,
    ) -> BuilderSession: ...

    async def get(self, tenant_id: str, session_id: UUID) -> BuilderSession | None: ...

    async def list_for_user(
        self, tenant_id: str, user_id: str, limit: int
    ) -> list[BuilderSession]: ...

    async def save(self, session: BuilderSession) -> None: ...

    async def add_message(
        self,
        tenant_id: str,
        session_id: UUID,
        role: str,
        content: str,
        events: list[dict[str, Any]],
    ) -> None: ...

    async def messages(self, tenant_id: str, session_id: UUID) -> list[dict[str, Any]]: ...


__all__ = [
    "ArchitectBrainPort",
    "BuilderSessionRepositoryPort",
    "Catalog",
    "CatalogConnection",
    "CatalogSkill",
    "CatalogTool",
    "SessionStatus",
    "UsageMeter",
]
