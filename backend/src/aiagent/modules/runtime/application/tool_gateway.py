"""ToolGateway — mọi tool call đều đi qua đây (ADR-0010).

Phase 1: kiểm input theo JSON Schema, giới hạn thời gian và độ dài kết quả, ẩn hẳn tool có risk
cần duyệt (chưa có HITL đến Phase 3), ghi audit. Phase 3 bổ sung Policy, HITL, Credential Broker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from aiagent.modules.agents.domain import AgentSpec
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.runtime.domain import ContentBlock, ToolDefinition
from aiagent.plugin_sdk import Plugin, RiskLevel, ToolContext, ToolSpec

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 16_000
_API_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def api_tool_name(tool_name: str) -> str:
    """`echo.say` → `echo__say` (tên tool của API không cho phép dấu chấm)."""
    return _API_NAME_RE.sub("_", tool_name.replace(".", "__"))[:128]


class PluginLookupPort(Protocol):
    def get(self, plugin_id: str) -> Plugin | None: ...


@dataclass(frozen=True)
class _Bound:
    plugin: Plugin
    spec: ToolSpec
    validator: Draft202012Validator


@dataclass
class BoundTools:
    """Bộ tool của một run, đã lọc theo AgentSpec. Hiện thực `ToolExecutorPort`."""

    ctx: ToolContext
    audit: AuditService
    timeout_s: float
    tools: dict[str, _Bound] = field(default_factory=dict)
    hidden: list[str] = field(default_factory=list)  # tool bị ẩn do cần duyệt

    @property
    def definitions(self) -> list[ToolDefinition]:
        # Thứ tự cố định → prompt cache ổn định.
        return [
            ToolDefinition(
                name=name, description=b.spec.description, input_schema=b.spec.input_schema
            )
            for name, b in sorted(self.tools.items())
        ]

    async def execute(self, tool_use: ContentBlock) -> tuple[ContentBlock, dict[str, object]]:
        tool_use_id = str(tool_use.get("id", ""))
        api_name = str(tool_use.get("name", ""))
        arguments = tool_use.get("input")
        bound = self.tools.get(api_name)
        started = time.monotonic()

        if bound is None:
            return self._result(tool_use_id, f"Tool '{api_name}' không tồn tại.", True), {
                "name": api_name,
                "is_error": True,
            }
        if not isinstance(arguments, dict):
            return self._result(tool_use_id, "Input của tool phải là object JSON.", True), {
                "name": bound.spec.name,
                "is_error": True,
            }
        errors = sorted(bound.validator.iter_errors(arguments), key=lambda e: list(e.path))
        if errors:
            detail = "; ".join(
                f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors[:5]
            )
            return self._result(tool_use_id, f"Input không hợp lệ: {detail}", True), {
                "name": bound.spec.name,
                "is_error": True,
            }

        try:
            async with asyncio.timeout(self.timeout_s):
                result = await bound.plugin.call_tool(bound.spec.name, arguments, self.ctx)
            content, is_error = result.content, result.is_error
        except TimeoutError:
            content, is_error = f"Tool quá thời gian ({self.timeout_s:.0f}s).", True
        except Exception:
            logger.exception("Tool %s lỗi", bound.spec.name)
            content, is_error = "Tool gặp lỗi nội bộ.", True

        if len(content) > MAX_RESULT_CHARS:
            content = content[:MAX_RESULT_CHARS] + "\n…[đã cắt bớt]"
        duration_ms = int((time.monotonic() - started) * 1000)

        await self.audit.record_safely(
            tenant_id=self.ctx.tenant_id,
            actor_id=self.ctx.user_id,
            action="tool.invoked",
            outcome=Outcome.FAILURE if is_error else Outcome.SUCCESS,
            resource_type="tool",
            resource_id=bound.spec.name,
            details={
                "agent_id": self.ctx.agent_id,
                "run_id": self.ctx.run_id,
                "risk": bound.spec.risk.value,
                "duration_ms": duration_ms,
                "arguments": arguments,
            },
        )
        return self._result(tool_use_id, content, is_error), {
            "name": bound.spec.name,
            "is_error": is_error,
            "duration_ms": duration_ms,
            "preview": content[:500],
        }

    @staticmethod
    def _result(tool_use_id: str, content: str, is_error: bool) -> ContentBlock:
        block: ContentBlock = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            block["is_error"] = True
        return block


class ToolGateway:
    def __init__(self, plugins: PluginLookupPort, audit: AuditService, timeout_s: float = 30.0):
        self._plugins = plugins
        self._audit = audit
        self._timeout_s = timeout_s

    def bind(self, spec: AgentSpec, ctx: ToolContext) -> BoundTools:
        bound = BoundTools(ctx=ctx, audit=self._audit, timeout_s=self._timeout_s)
        needs_approval: set[RiskLevel] = set(spec.approval.require_for)
        for binding in spec.skills:
            plugin = self._plugins.get(binding.skill_id)
            if plugin is None:
                logger.warning("Skill %s không còn hoạt động — bỏ qua", binding.skill_id)
                continue
            allowed = set(binding.allowed_tools)
            for tool in plugin.manifest.tools:
                if allowed and tool.name not in allowed:
                    continue
                if tool.risk in needs_approval:
                    bound.hidden.append(tool.name)  # Phase 3: HITL thay vì ẩn
                    continue
                try:
                    Draft202012Validator.check_schema(tool.input_schema)
                except SchemaError:
                    logger.error("Schema của tool %s không hợp lệ — bỏ qua", tool.name)
                    continue
                bound.tools[api_tool_name(tool.name)] = _Bound(
                    plugin=plugin, spec=tool, validator=Draft202012Validator(tool.input_schema)
                )
        return bound


def tool_use_summary(tool_use: ContentBlock) -> dict[str, Any]:
    raw = json.dumps(tool_use.get("input", {}), ensure_ascii=False, default=str)
    return {"id": tool_use.get("id"), "name": tool_use.get("name"), "input_preview": raw[:500]}
