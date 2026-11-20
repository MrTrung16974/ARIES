"""Dữ liệu trung gian của pipeline Architect. Các model đầu ra của LLM không có giá trị mặc định
(mọi trường đều `required` trong structured outputs); kiểm lại bằng Pydantic phía client.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Intent(StrEnum):
    CREATE = "create"
    EDIT = "edit"
    EXPLAIN = "explain"
    TEST = "test"
    PUBLISH = "publish"
    OUT_OF_SCOPE = "out_of_scope"


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentResult(_Out):
    intent: Intent
    test_message: str | None = Field(description="Câu cần chạy thử nếu intent=test, ngược lại null")


class DataSourceHint(_Out):
    type: Literal["database", "documents", "email", "web", "other"]
    hint: str


class ClarifyOption(_Out):
    label: str
    value: str


class ClarifyQuestion(_Out):
    id: str = Field(description="Mã ngắn, chữ thường, ví dụ q1")
    question: str
    options: list[ClarifyOption] = Field(description="2-4 lựa chọn gợi ý")


class RequirementBrief(_Out):
    goal: str
    suggested_name: str
    audience: Literal["internal", "external", "unknown"]
    language: str = Field(description="Mã ngôn ngữ trả lời, ví dụ vi hoặc en")
    tone: str
    capabilities_needed: list[str]
    data_sources: list[DataSourceHint]
    actions: list[str] = Field(description="Hành động tác động ra ngoài (gửi mail, ghi DB...)")
    constraints: list[str] = Field(description="Ràng buộc người dùng nêu, giữ nguyên ý")
    assumptions: list[str] = Field(description="Giả định đã tự chọn thay vì hỏi")
    questions: list[ClarifyQuestion] = Field(
        description="Tối đa 3 câu, CHỈ khi thiếu thông tin làm đổi cấu trúc agent; còn lại để trống"
    )


class SkillChoice(_Out):
    skill_id: str
    allowed_tools: list[str] = Field(description="Rỗng = mọi tool của skill")
    reason: str


class MissingItem(_Out):
    kind: Literal["connection", "knowledge_base", "skill", "other"]
    description: str


class CapabilityPlan(_Out):
    skills: list[SkillChoice]
    model: str
    effort: Literal["low", "medium", "high"]
    missing: list[MissingItem] = Field(description="Thứ cần nhưng chưa có trong catalog")


class PromptDraft(_Out):
    name: str
    description: str
    persona: str
    instructions: str
    rules: list[str]
    style: str
    opening_statement: str
    starter_prompts: list[str] = Field(description="3-4 câu gợi ý cho người dùng")


class PatchOp(_Out):
    op: Literal["add", "replace", "remove"]
    path: str = Field(description="JSON Pointer, ví dụ /rules/- hoặc /output_format/style")
    value_json: str = Field(
        description='Giá trị mới dạng JSON, ví dụ "\\"Ngắn gọn\\"" hoặc "[\\"a\\"]"; "null" nếu remove'
    )

    @property
    def value(self) -> Any:
        try:
            return json.loads(self.value_json)
        except json.JSONDecodeError:
            return self.value_json  # chấp nhận chuỗi thô


class EditProposal(_Out):
    scope: list[str] = Field(description="Các path gốc sẽ bị đổi, ví dụ /rules, /skills")
    ops: list[PatchOp]
    summary: str


class EvalCase(_Out):
    id: str
    kind: Literal["happy", "edge", "rule"]
    input: str
    expectation: str
    expected_tools: list[str] = Field(description="Tên tool (dạng skill.tool) nên được gọi")


class EvalCaseSet(_Out):
    cases: list[EvalCase]


class EvalVerdict(_Out):
    passed: bool
    score: float = Field(description="0..1")
    reason: str


# ------------------------------------------------------------------ không phải đầu ra LLM
class EvalCaseResult(BaseModel):
    case: EvalCase
    answer: str
    tools_used: list[str]
    passed: bool
    score: float
    reason: str
    error: str | None = None


class EvalReport(BaseModel):
    score: float
    passed: bool
    results: list[EvalCaseResult]
    threshold: float = 0.8

    @property
    def summary(self) -> str:
        ok = sum(r.passed for r in self.results)
        return f"{ok}/{len(self.results)} đạt"


class SessionStatus(StrEnum):
    DRAFTING = "drafting"
    AWAITING_USER = "awaiting_user"
    READY = "ready"
    PUBLISHED = "published"


@dataclass
class BuilderSession:
    id: UUID
    tenant_id: str
    user_id: str
    agent_id: UUID | None
    base_version: int | None
    status: SessionStatus
    brief: dict[str, Any] | None
    draft_spec: dict[str, Any] | None
    questions: list[dict[str, Any]]
    clarify_rounds: int
    eval_report: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    user_messages: list[str] = field(default_factory=list)
