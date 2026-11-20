"""OAuth broker (ADR-0008, ADR-0018): Authorization Code + PKCE, `state` dùng một lần.

Luồng: người dùng bấm "Kết nối với X" → `start` trả URL đăng nhập của nhà cung cấp → nhà cung
cấp chuyển về `/api/v1/oauth/callback` (không có Bearer token; danh tính lấy từ `state` đã gắn
với người bấm) → đổi code lấy token → tạo connection, token nằm ở OpenBao.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from aiagent.modules.connections.application.service import ConnectionService
from aiagent.modules.connections.domain import (
    Connection,
    ConnectionScope,
    OAuthClientPort,
    OAuthProvider,
    OAuthStatePort,
)
from aiagent.modules.identity.domain import Principal, Role
from aiagent.shared_kernel.errors import AppError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

STATE_TTL_S = 600


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OAuthBroker:
    def __init__(
        self,
        connections: ConnectionService,
        states: OAuthStatePort,
        client: OAuthClientPort,
        *,
        redirect_uri: str,
    ) -> None:
        self._connections = connections
        self._states = states
        self._client = client
        self.redirect_uri = redirect_uri

    @property
    def providers(self) -> dict[str, OAuthProvider]:
        return self._connections.providers

    def _provider(self, provider_id: str) -> OAuthProvider:
        provider = self.providers.get(provider_id)
        if provider is None:
            raise NotFoundError(f"Chưa cấu hình nhà cung cấp OAuth '{provider_id}'.")
        return provider

    async def start(
        self,
        principal: Principal,
        provider_id: str,
        *,
        connector_id: str,
        name: str,
        scope: ConnectionScope,
    ) -> str:
        """Trả URL đăng nhập của nhà cung cấp (kiểm quyền trước khi rời khỏi ARIES)."""
        provider = self._provider(provider_id)
        manifest = self._connections.manifest_of(connector_id)
        if manifest.oauth_provider != provider_id:
            raise ValidationError("Connector không dùng nhà cung cấp OAuth này.")
        ConnectionService.check_create_rights(principal, scope)
        ConnectionService.check_name(name)

        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        await self._states.put(
            state,
            {
                "tenant_id": principal.tenant_id,
                "subject": principal.subject,
                "username": principal.username,
                "roles": sorted(r.value for r in principal.roles),
                "provider": provider_id,
                "connector_id": connector_id,
                "name": name,
                "scope": scope.value,
                "verifier": verifier,
                "created": time.time(),
            },
            STATE_TTL_S,
        )
        scopes = list(dict.fromkeys([*provider.scopes, *manifest.oauth_scopes]))
        query = {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            **provider.extra_params,
        }
        sep = "&" if "?" in provider.authorize_url else "?"
        return f"{provider.authorize_url}{sep}{urlencode(query)}"

    async def callback(
        self, *, state: str | None, code: str | None, error: str | None
    ) -> Connection:
        if not state:
            raise ValidationError("Thiếu tham số state.")
        pending = await self._states.pop(state)  # dùng một lần, kể cả khi lỗi
        if pending is None:
            raise ValidationError("Phiên kết nối không hợp lệ hoặc đã hết hạn — hãy thử lại.")
        if error or not code:
            raise ValidationError(f"Nhà cung cấp từ chối: {error or 'không có mã xác thực'}.")
        provider = self._provider(str(pending["provider"]))
        principal = Principal(
            subject=str(pending["subject"]),
            tenant_id=str(pending["tenant_id"]),
            username=pending.get("username"),
            email=None,
            roles=frozenset(
                Role(r) for r in pending.get("roles", []) if r in Role._value2member_map_
            ),
        )
        tokens = await self._client.exchange(
            provider, code, str(pending["verifier"]), self.redirect_uri
        )
        account = await self._account(provider, tokens.access_token)
        return await self._connections.create_oauth(
            principal,
            name=str(pending["name"]),
            connector_id=str(pending["connector_id"]),
            scope=ConnectionScope(str(pending["scope"])),
            provider=provider,
            account=account,
            tokens=tokens,
        )

    async def _account(self, provider: OAuthProvider, access_token: str) -> str:
        if not provider.userinfo_url:
            return ""
        try:
            info: dict[str, Any] = await self._client.userinfo(provider, access_token)
        except AppError:
            logger.warning("Không đọc được userinfo từ %s", provider.id)
            return ""
        return str(info.get("email") or info.get("preferred_username") or info.get("sub") or "")


__all__ = ["OAuthBroker", "pkce_challenge"]
