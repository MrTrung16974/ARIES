from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from aiagent.plugin_sdk.manifest import PluginManifest


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    detail: str = ""


@dataclass(frozen=True)
class PluginContext:
    """Thông tin core cung cấp cho plugin lúc khởi động.

    Không chứa secret: secret được Credential Broker gắn vào lúc gọi tool (ADR-0008).
    """

    config: dict[str, Any] = field(default_factory=dict)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("aiagent.plugin"))


@dataclass(frozen=True)
class ToolContext:
    """Ngữ cảnh của một lần gọi tool. Không chứa secret (Credential Broker gắn ở adapter)."""

    tenant_id: str
    user_id: str
    agent_id: str
    run_id: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """Kết quả trả về cho model. `content` là văn bản; `is_error` báo lỗi cho model tự xử lý."""

    content: str
    is_error: bool = False
    data: dict[str, Any] | None = None  # dữ liệu có cấu trúc cho UI/log (không gửi model)


class ToolNotFoundError(LookupError):
    pass


class Plugin(ABC):
    """Lớp gốc của mọi plugin. Entry point trỏ tới lớp con (không tham số) hoặc instance."""

    @property
    @abstractmethod
    def manifest(self) -> PluginManifest: ...

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Thực thi tool `name` (khai báo trong manifest). Input đã được core kiểm theo schema."""
        raise ToolNotFoundError(name)

    async def on_load(self, ctx: PluginContext) -> None:  # noqa: B027 - hook tùy chọn
        """Gọi một lần khi core khởi động."""

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True)

    async def on_unload(self) -> None:  # noqa: B027 - hook tùy chọn
        """Gọi khi core tắt."""
