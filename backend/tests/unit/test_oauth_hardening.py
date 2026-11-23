"""OAuth trong Credential Broker: làm mới token đồng thời và khóa cấu hình OAuth (ADR-0018)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from aiagent.bootstrap.container import PluginConnectorLookup
from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.modules.audit.application import AuditService
from aiagent.modules.connections.application import ConnectionService
from aiagent.modules.connections.domain import ConnectionScope, OAuthProvider, TokenSet
from aiagent.modules.identity.domain import Principal, Role
from aiagent_plugins.connector_oidc import SsoProfileConnector
from tests.fakes import (
    InMemoryAuditLog,
    InMemoryConnectionRepository,
    InMemoryVault,
    _StaticEntryPoint,
)

PROVIDER = OAuthProvider(
    id="keycloak",
    name="Keycloak",
    authorize_url="http://idp/auth",
    token_url="http://idp/token",
    userinfo_url="http://idp/userinfo",
    client_id="c",
    client_secret="s",
)
BUILDER = Principal(
    subject="b1", tenant_id="t1", username=None, email=None, roles=frozenset({Role.BUILDER})
)


class RotatingOAuth:
    """Nhà cung cấp xoay vòng refresh token: mỗi refresh token chỉ dùng được một lần."""

    def __init__(self) -> None:
        self.valid = "rt-1"
        self.refreshes = 0

    async def refresh(self, provider: Any, refresh_token: str) -> TokenSet:
        await asyncio.sleep(0.01)  # mở cửa cho các lượt gọi đồng thời chen vào
        if refresh_token != self.valid:
            raise AssertionError(f"invalid_grant: {refresh_token}")
        self.refreshes += 1
        self.valid = f"rt-{self.refreshes + 1}"
        return TokenSet(f"at-{self.refreshes + 1}", self.valid, time.time() + 3600)


def _service(oauth: RotatingOAuth) -> ConnectionService:
    plugin = SsoProfileConnector()
    group = plugin.manifest.entry_point_group
    plugins = PluginRegistry(
        source=lambda g: [_StaticEntryPoint("sso", g, plugin)] if g == group else []
    )
    plugins.load_all()
    return ConnectionService(
        InMemoryConnectionRepository(),
        InMemoryVault(),
        PluginConnectorLookup(plugins),
        AuditService(InMemoryAuditLog()),
        oauth=oauth,  # type: ignore[arg-type]
        providers={"keycloak": PROVIDER},
    )


async def _connect(svc: ConnectionService) -> Any:
    return await svc.create_oauth(
        BUILDER,
        name="SSO của tôi",
        connector_id="sso",
        scope=ConnectionScope.PERSONAL,
        provider=PROVIDER,
        account="b1@corp.vn",
        tokens=TokenSet("at-1", "rt-1", expires_at=0.0),  # đã hết hạn
    )


async def test_concurrent_calls_refresh_once() -> None:
    oauth = RotatingOAuth()
    svc = _service(oauth)
    c = await _connect(svc)
    contexts = await asyncio.gather(*(svc.open(BUILDER, str(c.id)) for _ in range(5)))
    assert oauth.refreshes == 1
    assert {ctx.secrets["access_token"] for ctx in contexts} == {"at-2"}
    assert all(set(ctx.secrets) == {"access_token"} for ctx in contexts)  # không lộ refresh token


async def test_patch_config_cannot_touch_oauth_keys() -> None:
    svc = _service(RotatingOAuth())
    c = await _connect(svc)
    saved = await svc.update(
        BUILDER,
        c.id,
        config={"userinfo_url": "https://evil.example/steal", "note": "x"},
    )
    assert saved.config["userinfo_url"] == "http://idp/userinfo"
    assert saved.config["oauth_provider"] == "keycloak" and saved.config["note"] == "x"
    saved = await svc.update(BUILDER, c.id, config={})  # xóa hết cũng không mất khóa OAuth
    assert saved.config["oauth_provider"] == "keycloak"
    ctx = await svc.open(BUILDER, str(c.id))
    assert set(ctx.secrets) == {"access_token"}
