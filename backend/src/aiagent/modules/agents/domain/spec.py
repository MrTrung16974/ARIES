"""AgentSpec v2 — cấu hình agent là dữ liệu (docs/architecture.md mục 7).

Mọi đường tạo agent (Architect, form, API, CLI) đều phải ra một AgentSpec hợp lệ theo model này.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

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
    graph_type: Literal["react"] = "react"
    model: ModelSettings = Field(default_factory=ModelSettings)

    persona: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=20_000)
    rules: list[str] = Field(default_factory=list, max_length=50)
    output_format: OutputFormat = Field(default_factory=OutputFormat)
    opening_statement: str = Field(default="", max_length=1000)
    starter_prompts: list[str] = Field(default_factory=list, max_length=6)

    skills: list[SkillBinding] = Field(default_factory=list, max_length=30)
    knowledge_bases: list[str] = Field(default_factory=list, max_length=20)  # Phase 4

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
        return self
