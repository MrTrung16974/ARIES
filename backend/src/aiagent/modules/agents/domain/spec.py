"""AgentSpec v2 — cấu hình agent là dữ liệu (docs/architecture.md mục 7).

Mọi đường tạo agent (Architect, form, API, CLI) đều phải ra một AgentSpec hợp lệ theo model này.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aiagent.plugin_sdk import RiskLevel

SPEC_SCHEMA_VERSION = 2

# Tool rủi ro luôn phải qua duyệt — không cấu hình nào tắt được (ADR-0010).
MANDATORY_APPROVAL: frozenset[RiskLevel] = frozenset(
    {RiskLevel.EXTERNAL_SEND, RiskLevel.DESTRUCTIVE}
)


class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class DataClassification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSettings(_Strict):
    provider: Literal["anthropic", "fake"] = "anthropic"
    model: str = Field(default="claude-opus-5-5", pattern=r"^[a-z0-9][a-z0-9.\-]{1,63}$")
    # Model Claude hiện tại không nhận temperature/top_p — điều chỉnh bằng effort (ADR-0012).
    effort: Effort = Effort.MEDIUM
    max_tokens: int = Field(default=16000, ge=1024, le=128000)


class SkillBinding(_Strict):
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,62}$")
    # Rỗng = mọi tool của skill (trừ tool bị policy chặn).
    allowed_tools: list[str] = Field(default_factory=list, max_length=50)
    config: dict[str, Any] = Field(default_factory=dict)


class ConnectionBinding(_Strict):
    """Gắn một connection (Postgres…) — quyền dùng kiểm cả lúc lưu và lúc chạy (ADR-0016)."""

    connection_id: UUID
    allowed_tools: list[str] = Field(default_factory=list, max_length=50)


class TeamMember(_Strict):
    agent_id: UUID
    # Ghi đè when_to_use của agent thành viên (rỗng = dùng của agent).
    when_to_use: str = Field(default="", max_length=300)


class Team(_Strict):
    """Thành viên của agent supervisor (Master) — ADR-0017."""

    # auto: mọi agent đã publish mà người chat được dùng; fixed: chỉ `members`.
    mode: Literal["auto", "fixed"] = "auto"
    members: list[TeamMember] = Field(default_factory=list, max_length=30)
    exclude: list[UUID] = Field(default_factory=list, max_length=100)
    max_delegations_per_run: int = Field(default=8, ge=1, le=30)
    max_parallel: int = Field(default=3, ge=1, le=6)


class OutputFormat(_Strict):
    language: str = Field(default="vi", max_length=16)
    style: str = Field(default="", max_length=500)


class Limits(_Strict):
    max_iterations: int = Field(default=10, ge=1, le=50)
    timeout_s: int = Field(default=120, ge=5, le=900)
    max_tokens_per_run: int = Field(default=200_000, ge=1000, le=2_000_000)


class Approval(_Strict):
    require_for: list[RiskLevel] = Field(default_factory=lambda: sorted(MANDATORY_APPROVAL))

    @field_validator("require_for")
    @classmethod
    def _always_include_mandatory(cls, value: list[RiskLevel]) -> list[RiskLevel]:
        return sorted(set(value) | MANDATORY_APPROVAL)


class AgentSpec(_Strict):
    schema_version: Literal[2] = 2  # = SPEC_SCHEMA_VERSION
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    graph_type: Literal["react", "supervisor"] = "react"
    # Một câu: khi nào Master nên giao việc cho agent này (ADR-0017).
    when_to_use: str = Field(default="", max_length=300)
    model: ModelSettings = Field(default_factory=ModelSettings)

    persona: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=20_000)
    rules: list[str] = Field(default_factory=list, max_length=50)
    output_format: OutputFormat = Field(default_factory=OutputFormat)
    opening_statement: str = Field(default="", max_length=1000)
    starter_prompts: list[str] = Field(default_factory=list, max_length=6)

    skills: list[SkillBinding] = Field(default_factory=list, max_length=30)
    connections: list[ConnectionBinding] = Field(default_factory=list, max_length=20)
    knowledge_bases: list[str] = Field(default_factory=list, max_length=20)  # Phase 4
    team: Team | None = None  # chỉ cho graph_type supervisor

    limits: Limits = Field(default_factory=Limits)
    approval: Approval = Field(default_factory=Approval)
    data_classification: DataClassification = DataClassification.INTERNAL

    @field_validator("rules", "starter_prompts")
    @classmethod
    def _strip_items(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value if v.strip()]
        if any(len(v) > 500 for v in cleaned):
            raise ValueError("mỗi mục tối đa 500 ký tự")
        return cleaned

    @model_validator(mode="after")
    def _unique_skills(self) -> AgentSpec:
        ids = [s.skill_id for s in self.skills]
        if len(ids) != len(set(ids)):
            raise ValueError("skill_id bị trùng")
        conns = [c.connection_id for c in self.connections]
        if len(conns) != len(set(conns)):
            raise ValueError("connection_id bị trùng")
        if self.graph_type == "supervisor":
            # Master chỉ giao việc; tool/kết nối nằm ở agent thành viên (quyền rõ ràng, độ sâu 1).
            if self.skills or self.connections:
                raise ValueError("agent supervisor không gắn skill/kết nối trực tiếp")
            if self.team is None:
                self.team = Team()
            member_ids = [m.agent_id for m in self.team.members]
            if len(member_ids) != len(set(member_ids)):
                raise ValueError("thành viên team bị trùng")
            if self.team.mode == "fixed" and not member_ids:
                raise ValueError("team fixed cần ít nhất một thành viên")
        elif self.team is not None:
            raise ValueError("chỉ agent supervisor mới có team")
        return self
