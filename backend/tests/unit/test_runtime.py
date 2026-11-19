from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest

from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.modules.agents.domain import AgentSpec, DataClassification
from aiagent.modules.audit.application import AuditService
from aiagent.modules.runtime.application import ModelRouter, ToolGateway, api_tool_name
from aiagent.modules.runtime.domain import (
    LoopInput,
    ModelEvent,
    ModelRequest,
    ModelTurn,
    ModelUnavailableError,
    RunEventType,
    TurnCompleted,
    estimate_cost,
    executable_tool_uses,
    history_for_model,
    pending_tool_uses,
    sanitize_for_echo,
)
from aiagent.modules.runtime.infrastructure.anthropic_model import AnthropicChatModel
from aiagent.modules.runtime.infrastructure.fake_model import FakeChatModel
from aiagent.modules.runtime.infrastructure.react_graph import LangGraphReactLoop
from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from aiagent.shared_kernel.errors import ForbiddenError
from aiagent.shared_kernel.usage import Usage
from aiagent_plugins.skill_clock import ClockSkill
from tests.fakes import InMemoryAuditLog

CTX = ToolContext(tenant_id="default", user_id="u1", agent_id="a1", run_id="r1")


# ---------------------------------------------------------------- content blocks
def test_fallback_boundary_rules() -> None:
    content = [
        {"type": "thinking", "thinking": "", "signature": "s1"},
        {"type": "text", "text": "Phần đầu"},
        {"type": "tool_use", "id": "t_old", "name": "x", "input": {}},
        {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
        {"type": "thinking", "thinking": "", "signature": "s2"},
        {"type": "tool_use", "id": "t_new", "name": "x", "input": {}},
    ]
    assert [b["type"] for b in sanitize_for_echo(content)] == [
        "text",
        "fallback",
        "thinking",
        "tool_use",
    ]
    assert [b["id"] for b in executable_tool_uses(content)] == ["t_new"]
    plain = [{"type": "text", "text": "a"}]
    assert sanitize_for_echo(plain) is plain


def test_history_drops_empty_assistant_and_detects_pending_tools() -> None:
    history = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": []},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}],
        },
    ]
    assert len(history_for_model(history)) == 2
    assert [b["id"] for b in pending_tool_uses(history)] == ["t1"]


def test_cost_estimate() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=1_000_000)
    assert estimate_cost("claude-opus-5-5", usage) == pytest.approx(4.0 + 2.0 + 0.2)
    assert estimate_cost("unknown", usage) == 0.0


# ---------------------------------------------------------------- tool gateway
class RiskyPlugin(Plugin):
    def __init__(self, delay: float = 0.0, output: str = "ok") -> None:
        self.delay, self.output = delay, output

    @property
    def manifest(self) -> PluginManifest:
        schema = {
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
            "additionalProperties": False,
        }
        return PluginManifest(
            id="risky",
            kind=PluginKind.SKILL,
            name="Risky",
            version="1.0.0",
            api_version=PLUGIN_API_VERSION,
            tools=(
                ToolSpec(
                    name="risky.read", description="đọc", risk=RiskLevel.READ, input_schema=schema
                ),
                ToolSpec(name="risky.send", description="gửi", risk=RiskLevel.EXTERNAL_SEND),
                ToolSpec(name="risky.drop", description="xóa", risk=RiskLevel.DESTRUCTIVE),
            ),
        )

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(self.delay)
        return ToolResult(self.output)


class Lookup:
    def __init__(self, *plugins: Plugin) -> None:
        self._p = {p.manifest.id: p for p in plugins}

    def get(self, plugin_id: str) -> Plugin | None:
        return self._p.get(plugin_id)


def gateway(*plugins: Plugin, timeout: float = 1.0) -> tuple[ToolGateway, InMemoryAuditLog]:
    log = InMemoryAuditLog()
    return ToolGateway(Lookup(*plugins), AuditService(log), timeout_s=timeout), log


def agent_spec(**kw: Any) -> AgentSpec:
    return AgentSpec.model_validate(
        {"name": "t", "model": {"provider": "fake", "model": "fake-echo"}, **kw}
    )


def test_api_tool_name() -> None:
    assert api_tool_name("echo.say") == "echo__say"
    assert api_tool_name("a.b-c d") == "a__b-c_d"


async def test_risky_tools_are_hidden_until_hitl() -> None:
    gw, _ = gateway(RiskyPlugin())
    bound = gw.bind(agent_spec(skills=[{"skill_id": "risky"}]), CTX)
    assert [d.name for d in bound.definitions] == ["risky__read"]
    assert sorted(bound.hidden) == ["risky.drop", "risky.send"]


async def test_allowed_tools_filter_and_unknown_skill_ignored() -> None:
    gw, _ = gateway(RiskyPlugin())
    spec = agent_spec(
        skills=[{"skill_id": "risky", "allowed_tools": ["risky.send"]}, {"skill_id": "ghost"}]
    )
    assert gw.bind(spec, CTX).definitions == []


async def test_execute_validates_input_and_audits() -> None:
    gw, log = gateway(RiskyPlugin())
    bound = gw.bind(agent_spec(skills=[{"skill_id": "risky"}]), CTX)

    bad, info = await bound.execute({"id": "t1", "name": "risky__read", "input": {"n": "x"}})
    assert bad["is_error"] and "không hợp lệ" in bad["content"]
    assert info["is_error"]

    missing, _ = await bound.execute({"id": "t2", "name": "risky__send", "input": {}})
    assert missing["is_error"]  # tool bị ẩn không gọi được kể cả khi model đoán tên

    ok, info = await bound.execute({"id": "t3", "name": "risky__read", "input": {"n": 1}})
    assert ok == {"type": "tool_result", "tool_use_id": "t3", "content": "ok"}
    assert log.actions() == ["tool.invoked"]
    assert log.records[0].event.details["arguments"] == {"n": 1}


async def test_execute_timeout_and_truncation() -> None:
    gw, _ = gateway(RiskyPlugin(delay=0.5), timeout=0.05)
    bound = gw.bind(agent_spec(skills=[{"skill_id": "risky"}]), CTX)
    slow, _ = await bound.execute({"id": "t", "name": "risky__read", "input": {"n": 1}})
    assert slow["is_error"] and "quá thời gian" in slow["content"]

    gw2, _ = gateway(RiskyPlugin(output="x" * 20_000))
    bound2 = gw2.bind(agent_spec(skills=[{"skill_id": "risky"}]), CTX)
    big, _ = await bound2.execute({"id": "t", "name": "risky__read", "input": {"n": 1}})
    assert len(big["content"]) < 17_000 and big["content"].endswith("[đã cắt bớt]")


async def test_clock_skill() -> None:
    clock = ClockSkill(now=lambda: datetime(2026, 11, 19, 1, 30, tzinfo=UTC))
    result = await clock.call_tool("clock.now", {}, CTX)
    assert result.content.startswith("Thứ Năm, 19/11/2026 08:30:00 (Asia/Ho_Chi_Minh")
    bad = await clock.call_tool("clock.now", {"timezone": "Mars/Base"}, CTX)
    assert bad.is_error


# ---------------------------------------------------------------- router
def test_router_blocks_restricted_on_cloud_and_missing_provider() -> None:
    router = ModelRouter({"fake": FakeChatModel()})
    cloud = AgentSpec.model_validate({"name": "x", "data_classification": "restricted"})
    with pytest.raises(ForbiddenError):
        router.select(cloud)
    with pytest.raises(ModelUnavailableError):
        router.select(AgentSpec(name="x"))
    local = agent_spec(data_classification=DataClassification.RESTRICTED)
    assert isinstance(router.select(local), FakeChatModel)


# ---------------------------------------------------------------- react loop
def _loop_input(model: Any, text: str, **kw: Any) -> LoopInput:
    plugins = PluginRegistry()
    plugins.load_all()
    gw = ToolGateway(plugins, AuditService(InMemoryAuditLog()))
    bound = gw.bind(agent_spec(skills=[{"skill_id": "echo"}, {"skill_id": "clock"}]), CTX)
    request = ModelRequest(
        model="fake-echo",
        system="sys",
        tools=bound.definitions,
        messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
    )
    return LoopInput(
        model=model,
        request=request,
        tools=bound,
        max_iterations=kw.get("max_iterations", 5),
        max_tokens_per_run=kw.get("max_tokens", 100_000),
    )


async def _collect(loop: LoopInput) -> list[Any]:
    return [e async for e in LangGraphReactLoop().run(loop)]


async def test_loop_runs_tool_then_answers() -> None:
    events = await _collect(_loop_input(FakeChatModel(), '/tool echo__say {"text": "xin chào"}'))
    types = [e.type for e in events]
    assert types[0] is RunEventType.TOOL_START
    assert RunEventType.TOOL_END in types
    messages = [e.data for e in events if e.type is RunEventType.MESSAGE]
    assert [m["role"] for m in messages] == ["assistant", "user", "assistant"]
    assert messages[1]["content"][0]["content"] == "xin chào"
    assert "xin chào" in messages[2]["content"][0]["text"]
    done = events[-1]
    assert done.type is RunEventType.DONE and done.data["status"] == "completed"
    assert done.data["usage"].output_tokens > 0


async def test_loop_plain_answer_streams_tokens() -> None:
    events = await _collect(_loop_input(FakeChatModel(), "chào bạn"))
    tokens = "".join(e.data["text"] for e in events if e.type is RunEventType.TEXT)
    assert tokens == "Bạn vừa nói: chào bạn"


class ScriptedModel:
    def __init__(self, *turns: ModelTurn) -> None:
        self._turns = list(turns)
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        yield TurnCompleted(self._turns[min(self.calls, len(self._turns)) - 1])


def _turn(stop: str, content: list[dict[str, Any]], out: int = 10) -> ModelTurn:
    return ModelTurn(content=content, stop_reason=stop, usage=Usage(10, out), model="m")


async def test_loop_refusal_is_not_saved() -> None:
    model = ScriptedModel(_turn("refusal", [{"type": "text", "text": "partial"}]))
    events = await _collect(_loop_input(model, "x"))
    assert not [e for e in events if e.type is RunEventType.MESSAGE]
    assert events[-1].data["status"] == "refused"


async def test_loop_stops_at_max_iterations() -> None:
    call = {"type": "tool_use", "id": "t", "name": "echo__say", "input": {"text": "a"}}
    model = ScriptedModel(_turn("tool_use", [call]))
    events = await _collect(_loop_input(model, "x", max_iterations=2))
    assert events[-1].data["status"] == "failed"
    assert "vòng tool" in events[-1].data["error"]
    assert model.calls == 2


async def test_loop_pause_turn_and_token_budget() -> None:
    model = ScriptedModel(
        _turn("pause_turn", [{"type": "text", "text": "…"}]),
        _turn("end_turn", [{"type": "text", "text": "xong"}]),
    )
    events = await _collect(_loop_input(model, "x"))
    assert model.calls == 2 and events[-1].data["status"] == "completed"

    greedy = ScriptedModel(
        _turn(
            "tool_use",
            [{"type": "tool_use", "id": "t", "name": "echo__say", "input": {"text": "a"}}],
            out=5000,
        )
    )
    events = await _collect(_loop_input(greedy, "x", max_tokens=1000))
    assert "giới hạn token" in events[-1].data["error"]


# ---------------------------------------------------------------- anthropic params
def test_anthropic_params_per_model() -> None:
    model = AnthropicChatModel("test-key", client=object())
    req = ModelRequest(
        model="claude-opus-5-5",
        system="sys",
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        tools=[],
        effort="low",
        max_tokens=4000,
    )
    p = model._params(req)
    assert p["output_config"] == {"effort": "low"}
    assert p["fallbacks"] == "default"
    assert p["thinking"]["type"] == "adaptive" and p["thinking"]["display"] == "updates"
    assert "temperature" not in p and "tool_choice" not in p
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert set(p["betas"]) == {
        "server-side-fallback-2026-07-01",
        "thinking-display-updates-2026-08-18",
        "thinking-binding-controls-2026-08-01",
    }

    haiku = model._params(ModelRequest(model="claude-haiku-5-5", system="s", messages=[]))
    assert "fallbacks" not in haiku and "betas" not in haiku
    assert haiku["thinking"] == {"type": "adaptive"}
