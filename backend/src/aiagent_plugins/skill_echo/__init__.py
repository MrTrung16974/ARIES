"""Skill mẫu: lặp lại văn bản. Dùng để kiểm tra cơ chế plugin end-to-end."""

from __future__ import annotations

from typing import Any

from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolNotFoundError,
    ToolResult,
    ToolSpec,
)

_MANIFEST = PluginManifest(
    id="echo",
    kind=PluginKind.SKILL,
    name="Echo",
    description="Trả lại nguyên văn bản đầu vào. Skill mẫu để kiểm tra hệ thống plugin.",
    version="1.0.0",
    api_version=PLUGIN_API_VERSION,
    tools=(
        ToolSpec(
            name="echo.say",
            description="Trả lại nguyên văn bản được truyền vào.",
            risk=RiskLevel.READ,
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 4000}},
                "required": ["text"],
                "additionalProperties": False,
            },
        ),
    ),
    ui_hints={"icon": "pi pi-replay", "category": "utility"},
)


class EchoSkill(Plugin):
    @property
    def manifest(self) -> PluginManifest:
        return _MANIFEST

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if name != "echo.say":
            raise ToolNotFoundError(name)
        return ToolResult(content=str(arguments["text"]))
