"""Connection: một phiên bản cấu hình của connector (Postgres, Gmail…) — ADR-0016.

Chỉ cấu hình không bí mật nằm trong DB; secret nằm ở kho bí mật (OpenBao), core giải mã
ngay lúc gọi tool qua Credential Broker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from aiagent.plugin_sdk import Plugin, PluginManifest


class ConnectionScope(StrEnum):
    PERSONAL = "personal"  # chỉ người tạo dùng được
    WORKSPACE = "workspace"  # mọi người trong tenant dùng được; Admin quản lý


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"
    DELETED = "deleted"


@dataclass(frozen=True)
class Connection:
    id: UUID
    tenant_id: str
    name: str
    connector_id: str
    scope: ConnectionScope
    owner_id: str
    config: dict[str, Any]
    secret_keys: list[str]  # tên các secret đã lưu (không bao giờ là giá trị)
    status: ConnectionStatus
    last_test_ok: bool | None
    last_test_note: str | None
    last_test_at: datetime | None
    created_at: datetime
    updated_at: datetime


def secret_path(tenant_id: str, connection_id: UUID) -> str:
    return f"aries/{tenant_id}/connections/{connection_id}"


# ---------------------------------------------------------------- OAuth (ADR-0018)
@dataclass(frozen=True)
class OAuthProvider:
    id: str
    name: str
    authorize_url: str  # trình duyệt mở
    token_url: str  # API gọi (qua egress proxy nếu ra Internet)
    userinfo_url: str | None
    client_id: str
    client_secret: str = field(repr=False)
    scopes: tuple[str, ...] = ()
    extra_params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenSet:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: float = 0.0  # epoch giây


class OAuthStatePort(Protocol):
    """Lưu `state` một lần dùng (TTL ngắn)."""

    async def put(self, state: str, data: dict[str, Any], ttl_s: int) -> None: ...

    async def pop(self, state: str) -> dict[str, Any] | None: ...


class OAuthClientPort(Protocol):
    async def exchange(
        self, provider: OAuthProvider, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet: ...

    async def refresh(self, provider: OAuthProvider, refresh_token: str) -> TokenSet: ...

    async def userinfo(self, provider: OAuthProvider, access_token: str) -> dict[str, Any]: ...


class ConnectionRepositoryPort(Protocol):
    async def create(self, connection: Connection) -> Connection: ...

    async def get(self, tenant_id: str, connection_id: UUID) -> Connection | None: ...

    async def list_all(self, tenant_id: str) -> list[Connection]:
        """Mọi connection chưa xóa của tenant."""
        ...

    async def save(self, connection: Connection) -> Connection:
        """Cập nhật name/config/secret_keys/status/last_test_*."""
        ...


class SecretVaultPort(Protocol):
    async def put(self, path: str, data: dict[str, str]) -> None: ...

    async def get(self, path: str) -> dict[str, str]:
        """{} nếu không có."""
        ...

    async def delete(self, path: str) -> None: ...


class ConnectorLookupPort(Protocol):
    def get(self, plugin_id: str) -> Plugin | None: ...

    def connectors(self) -> list[PluginManifest]:
        """Manifest các connector đang hoạt động."""
        ...


__all__ = [
    "Connection",
    "ConnectionRepositoryPort",
    "ConnectionScope",
    "ConnectionStatus",
    "ConnectorLookupPort",
    "OAuthClientPort",
    "OAuthProvider",
    "OAuthStatePort",
    "SecretVaultPort",
    "TokenSet",
    "secret_path",
]
