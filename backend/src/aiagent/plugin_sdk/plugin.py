from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from aiagent.plugin_sdk.manifest import PluginManifest, ToolSpec
from aiagent.plugin_sdk.net import Endpoint, NetworkPolicy


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
class ConnectionContext:
    """Kết nối đã được Credential Broker giải mã cho MỘT lần gọi tool của connector.

    `secrets` không xuất hiện trong repr/log. Plugin không được ghi log hay trả secret về model.
    """

    id: str
    name: str
    connector_id: str
    config: dict[str, Any]
    secrets: dict[str, str] = field(default_factory=dict, repr=False)
    # Chính sách đích mạng do core cấp: plugin gọi `await network.check(Endpoint(...))` cho mọi
    # đích phát sinh lúc chạy (redirect, URL trong tham số…). Đích trong cấu hình thì core đã kiểm
    # qua `Plugin.network_targets` trước khi giao connection.
    network: NetworkPolicy | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ToolContext:
    """Ngữ cảnh của một lần gọi tool. Secret chỉ có trong `connection` (nếu là tool connector)."""

    tenant_id: str
    user_id: str
    agent_id: str
    run_id: str
    config: dict[str, Any] = field(default_factory=dict)
    connection: ConnectionContext | None = None
    tool: ToolSpec | None = None  # tool đang được gọi (kể cả `binding` của tool động)


@dataclass(frozen=True)
class ToolResult:
    """Kết quả trả về cho model. `content` là văn bản; `is_error` báo lỗi cho model tự xử lý."""

    content: str
    is_error: bool = False
    data: dict[str, Any] | None = None  # dữ liệu có cấu trúc cho UI/log (không gửi model)


@dataclass(frozen=True)
class TriggerEvent:
    """Một sự kiện mới từ nguồn ngoài. `content` là dữ liệu KHÔNG tin cậy — core tự bọc nhãn."""

    id: str  # định danh ổn định (chống xử lý lặp)
    title: str
    content: str


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

    async def test_connection(self, connection: ConnectionContext) -> HealthStatus:
        """Connector: kiểm tra kết nối với cấu hình + secret (khi tạo/sửa connection)."""
        return HealthStatus(healthy=True, detail="Connector không hỗ trợ kiểm tra")

    async def discover_tools(self, connection: ConnectionContext) -> list[ToolSpec]:
        """Connector `dynamic_tools`: khám phá tool cho MỘT connection (đọc OpenAPI, gRPC
        reflection…). Tên tool phải có tiền tố `<manifest.id>.`; dùng `binding` để nhớ cách gọi."""
        return []

    def network_targets(self, config: dict[str, Any]) -> list[Endpoint]:
        """Các đích mạng mà cấu hình connection trỏ tới — core kiểm theo `NetworkPolicy` khi lưu
        cấu hình (không DNS) và trước mỗi lần dùng (có DNS). Connector nối mạng nên khai báo."""
        return []

    async def poll_trigger(
        self,
        name: str,
        connection: ConnectionContext,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[list[TriggerEvent], dict[str, Any]]:
        """Connector có trigger: trả sự kiện mới kể từ `state` và state mới (lưu ở core)."""
        raise NotImplementedError(name)

    async def on_unload(self) -> None:  # noqa: B027 - hook tùy chọn
        """Gọi khi core tắt."""
