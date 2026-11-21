from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PLUGIN_API_VERSION = 1
"""Phiên bản Plugin API mà core hiện tại hỗ trợ."""


class PluginKind(StrEnum):
    SKILL = "skill"
    CONNECTOR = "connector"
    CHANNEL = "channel"
    TRIGGER = "trigger"
    LLM_PROVIDER = "llm_provider"
    VECTOR_STORE = "vector_store"
    LOADER = "loader"
    CHUNKER = "chunker"
    GRAPH_TEMPLATE = "graph_template"
    NODE = "node"
    GUARDRAIL = "guardrail"


ENTRY_POINT_GROUPS: dict[PluginKind, str] = {
    PluginKind.SKILL: "aiagent.skills",
    PluginKind.CONNECTOR: "aiagent.connectors",
    PluginKind.CHANNEL: "aiagent.channels",
    PluginKind.TRIGGER: "aiagent.triggers",
    PluginKind.LLM_PROVIDER: "aiagent.llm_providers",
    PluginKind.VECTOR_STORE: "aiagent.vector_stores",
    PluginKind.LOADER: "aiagent.loaders",
    PluginKind.CHUNKER: "aiagent.chunkers",
    PluginKind.GRAPH_TEMPLATE: "aiagent.graph_templates",
    PluginKind.NODE: "aiagent.nodes",
    PluginKind.GUARDRAIL: "aiagent.guardrails",
}


class RiskLevel(StrEnum):
    """Mức rủi ro của một tool — quyết định có cần người duyệt hay không (ADR-0010)."""

    READ = "read"
    WRITE = "write"
    EXTERNAL_SEND = "external_send"
    DESTRUCTIVE = "destructive"


_ID_PATTERN = r"^[a-z][a-z0-9_]{1,62}$"
_TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
_SEMVER_PATTERN = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_TOOL_NAME_PATTERN, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    risk: RiskLevel
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    # Kết quả chứa dữ liệu từ nguồn không tin cậy (web, nội dung người dùng nhập vào DB...):
    # core bọc nhãn cảnh báo và đánh dấu run "nhiễm" (taint) — ADR-0016.
    untrusted_output: bool = False
    # Tool đọc nhưng gọi ra mạng theo URL trong tham số (web.fetch…): khi run đã nhiễm, chỉ được
    # gọi URL đã có nguyên văn trong hội thoại — chặn rò dữ liệu qua query string (ADR-0021).
    network: bool = False


class TriggerSpec(BaseModel):
    """Nguồn sự kiện của connector (mail mới…) — core gọi `Plugin.poll_trigger` định kỳ."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=_TOOL_NAME_PATTERN, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    config_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})


class PluginManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=_ID_PATTERN)
    kind: PluginKind
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    version: str = Field(pattern=_SEMVER_PATTERN)
    api_version: int = Field(ge=1)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    secrets: tuple[str, ...] = ()
    # Connector dùng OAuth (ADR-0018): broker lấy/làm mới access token, plugin chỉ nhận
    # `secrets["access_token"]` (không bao giờ thấy refresh token).
    oauth_provider: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,30}$")
    oauth_scopes: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    tools: tuple[ToolSpec, ...] = ()
    triggers: tuple[TriggerSpec, ...] = ()
    ui_hints: dict[str, Any] = Field(default_factory=dict)

    @property
    def entry_point_group(self) -> str:
        return ENTRY_POINT_GROUPS[self.kind]
