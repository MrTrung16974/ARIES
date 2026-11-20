"""Connector OAuth/OIDC (ADR-0018): đọc hồ sơ tài khoản đã kết nối.

Plugin chỉ nhận `secrets["access_token"]` do Credential Broker cấp (đã tự làm mới), không bao
giờ thấy refresh token. Là nền cho Gmail/Drive (Phase 4/6) dùng cùng cơ chế OAuth.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    ConnectionContext,
    HealthStatus,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolNotFoundError,
    ToolResult,
    ToolSpec,
)

_EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
_FIELDS = ("sub", "name", "email", "email_verified", "preferred_username", "locale")


class _OidcProfileConnector(Plugin):
    _ID = ""
    _PROVIDER = ""
    _NAME = ""
    _DESCRIPTION = ""
    _SCOPES: tuple[str, ...] = ("openid", "email", "profile")

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            id=self._ID,
            kind=PluginKind.CONNECTOR,
            name=self._NAME,
            description=self._DESCRIPTION,
            version="1.0.0",
            api_version=PLUGIN_API_VERSION,
            config_schema={"type": "object"},
            oauth_provider=self._PROVIDER,
            oauth_scopes=self._SCOPES,
            tools=(
                ToolSpec(
                    name=f"{self._ID}.profile",
                    description="Xem hồ sơ (tên, email) của tài khoản đã kết nối.",
                    risk=RiskLevel.READ,
                    untrusted_output=True,
                    input_schema=_EMPTY_SCHEMA,
                ),
            ),
            ui_hints={"icon": "person", "category": "account"},
        )

    async def _userinfo(self, conn: ConnectionContext) -> dict[str, Any]:
        url = conn.config.get("userinfo_url")
        token = conn.secrets.get("access_token")
        if not url or not token:
            raise ConnectionError("Kết nối thiếu thông tin OAuth.")
        async with httpx.AsyncClient(timeout=10.0) as http:  # tôn trọng egress proxy (env)
            r = await http.get(str(url), headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 401:
            raise ConnectionError("Token không còn hiệu lực — hãy kết nối lại.")
        if r.status_code != 200:
            raise ConnectionError(f"Nhà cung cấp trả về HTTP {r.status_code}.")
        data = r.json()
        return {k: data[k] for k in _FIELDS if k in data}

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if name != f"{self._ID}.profile":
            raise ToolNotFoundError(name)
        if ctx.connection is None:
            return ToolResult("Tool này cần một kết nối.", is_error=True)
        try:
            profile = await self._userinfo(ctx.connection)
        except (ConnectionError, httpx.HTTPError) as exc:
            return ToolResult(str(exc) or "Không gọi được nhà cung cấp.", is_error=True)
        return ToolResult(json.dumps(profile, ensure_ascii=False))

    async def test_connection(self, connection: ConnectionContext) -> HealthStatus:
        try:
            profile = await self._userinfo(connection)
        except (ConnectionError, httpx.HTTPError) as exc:
            return HealthStatus(False, str(exc) or "Không gọi được nhà cung cấp.")
        who = profile.get("email") or profile.get("preferred_username") or profile.get("sub")
        return HealthStatus(True, f"Kết nối OK — tài khoản {who}.")


class GoogleAccountConnector(_OidcProfileConnector):
    _ID = "google"
    _PROVIDER = "google"
    _NAME = "Google"
    _DESCRIPTION = "Tài khoản Google (OAuth). Gmail/Drive dùng chung kết nối này ở các phase sau."


class SsoProfileConnector(_OidcProfileConnector):
    _ID = "sso"
    _PROVIDER = "keycloak"
    _NAME = "SSO nội bộ"
    _DESCRIPTION = "Tài khoản đăng nhập một lần của công ty (Keycloak, OAuth)."
