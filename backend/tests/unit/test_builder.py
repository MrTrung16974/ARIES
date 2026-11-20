from __future__ import annotations

from typing import Any

import pytest

from aiagent.bootstrap.container import PluginCatalogProvider, PluginSkillCatalog
from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.modules.agents.application import AgentService
from aiagent.modules.audit.application import AuditService
from aiagent.modules.builder.application import ArchitectService, BuilderEvent, CommandService
from aiagent.modules.builder.domain import (
    Catalog,
    CatalogSkill,
    CatalogTool,
    CommandKind,
    PatchError,
    PatchOp,
    RequirementBrief,
    SessionStatus,
    UsageMeter,
    apply_patch,
    diff_specs,
    parse_command,
)
from aiagent.modules.builder.infrastructure.heuristic_brain import HeuristicArchitectBrain
from aiagent.modules.builder.infrastructure.llm_brain import LlmArchitectBrain
from aiagent.modules.conversations.application import ConversationService
from aiagent.modules.runtime.application import ModelRouter, RunAgentService, ToolGateway
from aiagent.modules.runtime.domain import StructuredRequest, StructuredResult, strict_schema
from aiagent.modules.runtime.infrastructure.fake_model import FakeChatModel
from aiagent.modules.runtime.infrastructure.react_graph import LangGraphReactLoop
from aiagent.shared_kernel.errors import ForbiddenError, NotFoundError, ValidationError
from tests.fakes import (
    InMemoryAgentRepository,
    InMemoryAuditLog,
    InMemoryBuilderSessionRepository,
    InMemoryConversationRepository,
    principal,
)

BUILDER = principal("b1", "builder")


def op(o: str, path: str, value: Any = None) -> PatchOp:
    import json

    return PatchOp(op=o, path=path, value_json=json.dumps(value, ensure_ascii=False))  # type: ignore[arg-type]


# ---------------------------------------------------------------- patch & diff
def test_apply_patch_within_scope() -> None:
    doc = {"rules": ["a"], "output_format": {"style": "dài"}, "name": "x"}
    out = apply_patch(
        doc,
        [op("add", "/rules/-", "b"), op("replace", "/output_format/style", "ngắn")],
        ["/rules", "/output_format"],
    )
    assert out == {"rules": ["a", "b"], "output_format": {"style": "ngắn"}, "name": "x"}
    assert doc["rules"] == ["a"]  # không đổi bản gốc


@pytest.mark.parametrize(
    ("ops", "scope", "message"),
    [
        ([op("replace", "/approval/require_for", [])], ["/approval"], "Không được sửa"),
        ([op("replace", "/schema_version", 3)], ["/"], "Không được sửa"),
        ([op("replace", "/name", "y")], ["/rules"], "ngoài phạm vi"),
        ([op("replace", "/missing", 1)], ["/missing"], "Không có khóa"),
        ([op("add", "rules/-", "x")], ["rules"], "không hợp lệ"),
    ],
)
def test_apply_patch_rejects(ops: list[PatchOp], scope: list[str], message: str) -> None:
    with pytest.raises(PatchError, match=message):
        apply_patch(
            {"name": "x", "rules": [], "approval": {"require_for": []}, "schema_version": 2},
            ops,
            scope,
        )


def test_diff_human_readable() -> None:
    changes = diff_specs(
        {"name": "a", "rules": ["x"], "output_format": {"style": ""}},
        {"name": "b", "rules": ["x", "y"], "output_format": {"style": ""}},
    )
    assert [c.human() for c in changes] == ['~ /name: "a" → "b"', '~ /rules: ["x"] → ["x", "y"]']


# ---------------------------------------------------------------- commands
@pytest.mark.parametrize(
    ("text", "kind", "target", "extra"),
    [
        ("tạo trợ lý CSKH", CommandKind.AGENT_NEW, None, "tạo trợ lý CSKH"),
        ("/agent new trợ lý tóm tắt email", CommandKind.AGENT_NEW, None, "trợ lý tóm tắt email"),
        (
            '/agent edit "CSKH đơn hàng" bớt dài dòng',
            CommandKind.AGENT_EDIT,
            "CSKH đơn hàng",
            "bớt dài dòng",
        ),
        ("/agent publish cskh", CommandKind.AGENT_PUBLISH, "cskh", None),
        ("/agent rollback cskh v2", CommandKind.AGENT_ROLLBACK, "cskh", 2),
        ("/agent list", CommandKind.AGENT_LIST, None, None),
        ("/help", CommandKind.HELP, None, None),
    ],
)
def test_parse_command(text: str, kind: CommandKind, target: str | None, extra: Any) -> None:
    cmd = parse_command(text)
    assert cmd.kind is kind and cmd.target == target
    if isinstance(extra, int):
        assert cmd.version == extra
    elif extra:
        assert cmd.text == extra


@pytest.mark.parametrize(
    "text",
    [
        "",
        "/agent",
        "/agent rollback cskh",
        "/agent publish cskh abc",
        '/agent edit "x',
        "/deploy",
        "/agent fly",
    ],
)
def test_parse_command_invalid(text: str) -> None:
    assert parse_command(text).kind is CommandKind.INVALID


# ---------------------------------------------------------------- structured schema
def test_strict_schema_removes_unsupported_and_requires_all() -> None:
    schema = strict_schema(RequirementBrief.model_json_schema())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    q = schema["$defs"]["ClarifyQuestion"]
    assert q["additionalProperties"] is False

    def keys(node: Any) -> set[str]:
        if isinstance(node, dict):
            return set(node) | {k for v in node.values() for k in keys(v)}
        if isinstance(node, list):
            return {k for v in node for k in keys(v)}
        return set()

    assert not keys(schema) & {"maxLength", "minLength", "default", "title"}


class ScriptedStructured:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[StructuredRequest] = []

    async def generate_json(self, request: StructuredRequest) -> StructuredResult:
        self.requests.append(request)
        return StructuredResult(self.responses.pop(0))


CATALOG = Catalog(
    skills=[CatalogSkill("clock", "Đồng hồ", "giờ", [CatalogTool("clock.now", "giờ", "read")])],
    models=["claude-opus-5-5", "claude-haiku-5-5"],
)


async def test_llm_plan_constrains_ids_with_enums() -> None:
    model = ScriptedStructured(
        [
            {
                "skills": [{"skill_id": "clock", "allowed_tools": [], "reason": "cần giờ"}],
                "model": "claude-opus-5-5",
                "effort": "medium",
                "missing": [],
            }
        ]
    )
    brain = LlmArchitectBrain(model)  # type: ignore[arg-type]
    brief = await HeuristicArchitectBrain().understand(["trợ lý báo giờ"], False, UsageMeter())
    plan = await brain.plan(brief, CATALOG, UsageMeter())
    assert plan.skills[0].skill_id == "clock"
    schema = model.requests[0].schema
    assert schema["properties"]["model"]["enum"] == CATALOG.models
    skill_choice = schema["$defs"]["SkillChoice"]["properties"]
    assert skill_choice["skill_id"]["enum"] == ["clock"]
    assert skill_choice["allowed_tools"]["items"]["enum"] == ["clock.now"]
    assert model.requests[0].model == "claude-opus-5-5"


async def test_llm_brain_invalid_output_raises() -> None:
    from aiagent.modules.runtime.domain import ModelUnavailableError

    brain = LlmArchitectBrain(ScriptedStructured([{"intent": "fly"}]))  # type: ignore[arg-type]
    with pytest.raises(ModelUnavailableError):
        await brain.classify("x", True, UsageMeter())


# ---------------------------------------------------------------- architect service
@pytest.fixture
def env() -> dict[str, Any]:
    plugins = PluginRegistry()
    plugins.load_all()
    log = InMemoryAuditLog()
    audit = AuditService(log)
    agents = AgentService(InMemoryAgentRepository(), PluginSkillCatalog(plugins))
    router = ModelRouter({"fake": FakeChatModel()})
    runs = RunAgentService(
        agents,
        ConversationService(InMemoryConversationRepository()),
        ToolGateway(plugins, audit),
        router,
        LangGraphReactLoop(),
        audit,
    )
    sessions = InMemoryBuilderSessionRepository()
    architect = ArchitectService(
        sessions,
        HeuristicArchitectBrain(),
        agents,
        runs,
        PluginCatalogProvider(plugins, router),
        audit,
    )
    return {
        "architect": architect,
        "agents": agents,
        "log": log,
        "sessions": sessions,
        "commands": CommandService(architect, agents, audit),
    }


async def collect(it: Any) -> list[BuilderEvent]:
    return [e async for e in it]


def of(events: list[BuilderEvent], kind: str) -> list[dict[str, Any]]:
    return [e.data for e in events if e.type == kind]


async def test_full_flow_clarify_draft_eval_edit_publish(env: dict[str, Any]) -> None:
    arch: ArchitectService = env["architect"]
    session = await arch.create_session(BUILDER)

    # 1. Mô tả thiếu đối tượng → hỏi lại (≤ 3 câu, có lựa chọn)
    events = await collect(
        arch.handle(BUILDER, session.id, message="Tạo trợ lý báo giờ và ngày hôm nay")
    )
    clarify = of(events, "clarify")
    assert clarify and 1 <= len(clarify[0]["questions"]) <= 3
    assert clarify[0]["questions"][0]["options"]
    assert (await arch.owned(BUILDER, session.id)).status is SessionStatus.AWAITING_USER

    # 2. Trả lời → dựng bản nháp, chọn skill clock, chạy eval
    events = await collect(arch.handle(BUILDER, session.id, answers={"q2": "nội bộ"}))
    draft = of(events, "spec_draft")[0]
    assert draft["spec"]["model"] == {
        "provider": "fake",
        "model": "fake-echo",
        "effort": "medium",
        "max_tokens": 16000,
    }
    assert [s["skill_id"] for s in draft["spec"]["skills"]] == ["clock"]
    assert draft["permissions"] == [
        {"skill": "clock", "tool": "clock.now", "risk": "read", "status": "active"}
    ]
    progress = of(events, "eval_progress")
    assert progress[0] == {"done": 0, "total": progress[-1]["total"]} and progress[-1]["done"] > 0
    report = of(events, "eval_result")[0]
    assert report["passed"] is True
    assert of(events, "done")[0]["status"] == "ready"
    assert not of(events, "clarify")  # đã hỏi một vòng thì không hỏi nữa

    # 3. Sửa bằng lời → patch đúng chỗ, diff, chạy lại cùng bộ eval
    events = await collect(
        arch.handle(BUILDER, session.id, message="bớt dài dòng, không bao giờ nói tục")
    )
    diff = of(events, "spec_diff")[0]
    assert any("/output_format/style" in c for c in diff["changes"])
    assert any("/rules" in c and "không bao giờ nói tục" in c for c in diff["changes"])
    assert (
        of(events, "eval_result")[0]["results"][0]["case"]["id"]
        == report["results"][0]["case"]["id"]
    )

    # 4. Publish: luôn qua xác nhận; tạo agent + version, ghi audit
    events = await collect(arch.handle(BUILDER, session.id, message="publish"))
    assert of(events, "confirm_required")[0]["eval_passed"] is True
    result = await arch.confirm_publish(BUILDER, session.id, override_eval=False)
    agent = await env["agents"].get(BUILDER, __import__("uuid").UUID(result["agent_id"]))
    assert (agent.published_version, result["version"]) == (1, 1)
    assert "architect.published" in env["log"].actions()

    # 5. Sửa tiếp sau publish → version 2
    await collect(arch.handle(BUILDER, session.id, message="đổi tên thành “Trợ lý giờ”"))
    second = await arch.confirm_publish(BUILDER, session.id, override_eval=False)
    assert second["version"] == 2


async def test_publish_requires_override_when_eval_fails(env: dict[str, Any]) -> None:
    arch: ArchitectService = env["architect"]
    session = await arch.create_session(BUILDER)
    await collect(arch.handle(BUILDER, session.id, message="Trợ lý nội bộ báo giờ"))
    stored = env["sessions"].sessions[session.id]
    stored.eval_report = {**stored.eval_report, "passed": False, "score": 0.3}
    with pytest.raises(ValidationError, match="Eval chưa đạt"):
        await arch.confirm_publish(BUILDER, session.id, override_eval=False)
    result = await arch.confirm_publish(BUILDER, session.id, override_eval=True)
    assert result["published"]
    audit = next(r.event for r in env["log"].records if r.event.action == "architect.published")
    assert audit.details["override_eval"] is True


async def test_edit_cannot_touch_approval(env: dict[str, Any], monkeypatch: Any) -> None:
    arch: ArchitectService = env["architect"]
    session = await arch.create_session(BUILDER)
    await collect(arch.handle(BUILDER, session.id, message="Trợ lý nội bộ báo giờ"))

    from aiagent.modules.builder.domain import EditProposal

    async def evil_edit(*_: Any) -> EditProposal:
        return EditProposal(
            scope=["/approval"],
            summary="tắt duyệt",
            ops=[op("replace", "/approval/require_for", [])],
        )

    monkeypatch.setattr(HeuristicArchitectBrain, "edit", evil_edit)
    events = await collect(arch.handle(BUILDER, session.id, message="tắt duyệt tool đi"))
    assert of(events, "validation")[0]["ok"] is False
    stored = env["sessions"].sessions[session.id]
    assert set(stored.draft_spec["approval"]["require_for"]) == {"destructive", "external_send"}


async def test_plan_outside_catalog_is_dropped(env: dict[str, Any], monkeypatch: Any) -> None:
    from aiagent.modules.builder.domain import CapabilityPlan, SkillChoice

    async def greedy_plan(*_: Any) -> CapabilityPlan:
        return CapabilityPlan(
            skills=[
                SkillChoice(skill_id="gmail", allowed_tools=[], reason="gửi mail"),
                SkillChoice(
                    skill_id="clock", allowed_tools=["clock.now", "clock.hack"], reason="giờ"
                ),
            ],
            model="gpt-9",
            effort="medium",
            missing=[],
        )

    monkeypatch.setattr(HeuristicArchitectBrain, "plan", greedy_plan)
    arch: ArchitectService = env["architect"]
    session = await arch.create_session(BUILDER)
    events = await collect(arch.handle(BUILDER, session.id, message="Trợ lý nội bộ báo giờ"))
    spec = of(events, "spec_draft")[0]["spec"]
    assert spec["skills"] == [{"skill_id": "clock", "allowed_tools": ["clock.now"], "config": {}}]
    assert spec["model"]["model"] == "fake-echo"


async def test_permissions_and_ownership(env: dict[str, Any]) -> None:
    arch: ArchitectService = env["architect"]
    with pytest.raises(ForbiddenError):
        await arch.create_session(principal("u1", "user"))
    session = await arch.create_session(BUILDER)
    with pytest.raises(NotFoundError):
        await arch.owned(principal("b2", "builder"), session.id)


async def test_test_and_explain_intents(env: dict[str, Any]) -> None:
    arch: ArchitectService = env["architect"]
    session = await arch.create_session(BUILDER)
    await collect(arch.handle(BUILDER, session.id, message="Trợ lý nội bộ báo giờ"))
    events = await collect(arch.handle(BUILDER, session.id, message="thử: /tool clock__now {}"))
    result = of(events, "test_result")[0]
    assert result["tools_used"] == ["clock.now"] and "Kết quả tool" in result["answer"]
    events = await collect(arch.handle(BUILDER, session.id, message="agent đang có quyền gì?"))
    assert "clock.now" in of(events, "message")[0]["text"]


async def test_commands(env: dict[str, Any]) -> None:
    commands: CommandService = env["commands"]
    arch: ArchitectService = env["architect"]
    out = await commands.execute(BUILDER, "/agent new trợ lý báo giờ")
    assert out["kind"] == "architect" and out["message"] == "trợ lý báo giờ"

    session = await arch.create_session(BUILDER)
    await collect(arch.handle(BUILDER, session.id, message="Trợ lý nội bộ báo giờ"))
    published = await arch.confirm_publish(BUILDER, session.id, override_eval=True)
    name = (await env["agents"].list_agents(BUILDER))[0].name

    listing = await commands.execute(BUILDER, "/agent list")
    assert name in listing["text"]
    chat = await commands.execute(BUILDER, f'/agent chat "{name}"')
    assert chat == {"kind": "navigate", "url": f"/agents/{published['agent_id']}/chat"}
    edit = await commands.execute(BUILDER, f'/agent edit "{name}" bớt dài dòng')
    assert edit["kind"] == "architect"
    missing = await commands.execute(BUILDER, "/agent publish không-có")
    assert missing["kind"] == "error"
    assert "command.executed" in env["log"].actions()


async def test_heuristic_brief_ignores_answers_in_rules() -> None:
    brain = HeuristicArchitectBrain()
    brief = await brain.understand(
        [
            "Tạo trợ lý báo giờ và ngày hôm nay. Không bàn chuyện chính trị",
            "Trả lời: Ai sẽ dùng agent này? → nội bộ",
        ],
        False,
        UsageMeter(),
    )
    assert brief.constraints == ["Không bàn chuyện chính trị"]
    assert brief.audience == "internal"
    assert brief.suggested_name == "Báo giờ"


async def test_heuristic_edit_splits_rule_at_comma() -> None:
    proposal = await HeuristicArchitectBrain().edit(
        {"name": "a", "rules": [], "output_format": {"style": ""}, "instructions": ""},
        'không bao giờ dùng từ lóng, đổi tên thành "Trợ lý giờ"',
        CATALOG,
        UsageMeter(),
    )
    values = {o.path: o.value for o in proposal.ops}
    assert values["/rules/-"] == "không bao giờ dùng từ lóng"
    assert values["/name"] == "Trợ lý giờ"
