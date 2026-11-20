"""MCP bridge (ADR-0018): MCP server do Admin đăng ký → tool dùng được trong agent."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from aiagent.plugin_sdk import RiskLevel

SLUG_PATTERN = r"^[a-z][a-z0-9_]{1,30}$"


class McpTransport(StrEnum):
    HTTP = "http"  # streamable HTTP
    STDIO = "stdio"  # lệnh nằm trong allowlist của người vận hành


@dataclass(frozen=True)
class McpTool:
    name: str  # tên gốc trên MCP server
    description: str
    input_schema: dict[str, Any]
    risk: RiskLevel  # theo annotation, Admin ghi đè được


@dataclass(frozen=True)
class McpServer:
    id: UUID
    tenant_id: str
    name: str
    slug: str
    transport: McpTransport
    url: str | None
    command_key: str | None
    secret_keys: list[str]  # tên header (http) hoặc biến môi trường (stdio) bí mật
    enabled: bool
    deleted: bool
    tools: list[McpTool]
    risk_overrides: dict[str, str]
    last_sync_at: datetime | None
    last_error: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    def effective_risk(self, tool: McpTool) -> RiskLevel:
        override = self.risk_overrides.get(tool.name)
        return RiskLevel(override) if override else tool.risk


@dataclass(frozen=True)
class McpEndpoint:
    """Cách kết nối tới server cho MỘT lần gọi (đã kèm secret — không log)."""

    transport: McpTransport
    url: str | None = None
    command: list[str] = field(default_factory=list)
    secrets: dict[str, str] = field(default_factory=dict, repr=False)


def tool_manifest_name(slug: str, tool_name: str) -> str:
    """`get-weather` của server `demo` → `mcp.demo.get_weather` (khớp quy ước tên tool của SDK)."""
    clean = re.sub(r"[^a-z0-9_]+", "_", tool_name.lower()).strip("_") or "tool"
    if not clean[0].isalpha():
        clean = f"t_{clean}"
    return f"mcp.{slug}.{clean[:80]}"


def risk_from_annotations(read_only: bool | None, destructive: bool | None) -> RiskLevel:
    if read_only:
        return RiskLevel.READ
    if destructive:
        return RiskLevel.DESTRUCTIVE
    return RiskLevel.WRITE  # không khai báo → coi là có tác dụng phụ


def secret_path(tenant_id: str, server_id: UUID) -> str:
    return f"aries/{tenant_id}/mcp/{server_id}"


class McpServerRepositoryPort(Protocol):
    async def create(self, server: McpServer) -> McpServer: ...

    async def get(self, tenant_id: str, server_id: UUID) -> McpServer | None: ...

    async def list_all(self, tenant_id: str) -> list[McpServer]: ...

    async def save(self, server: McpServer) -> McpServer: ...


class McpClientPort(Protocol):
    async def list_tools(self, endpoint: McpEndpoint) -> list[McpTool]: ...

    async def call_tool(
        self, endpoint: McpEndpoint, name: str, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        """(văn bản kết quả, is_error)."""
        ...


__all__ = [
    "SLUG_PATTERN",
    "McpClientPort",
    "McpEndpoint",
    "McpServer",
    "McpServerRepositoryPort",
    "McpTool",
    "McpTransport",
    "risk_from_annotations",
    "secret_path",
    "tool_manifest_name",
]
