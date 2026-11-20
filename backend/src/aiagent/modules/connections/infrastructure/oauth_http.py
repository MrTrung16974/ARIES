"""OAuth 2.0 qua HTTP (token endpoint, userinfo) + kho `state` trên Redis (ADR-0018)."""

from __future__ import annotations

import contextlib
import json
import logging
import time
from typing import Any, cast

import httpx
from redis.asyncio import Redis

from aiagent.modules.connections.domain import OAuthProvider, TokenSet
from aiagent.shared_kernel.errors import AppError

logger = logging.getLogger(__name__)


class OAuthError(AppError):
    code = "oauth_error"
    status_code = 502
    public_message = "Nhà cung cấp OAuth không phản hồi đúng."


class HttpOAuthClient:
    """`http` nên tôn trọng biến môi trường proxy (ra Internet qua egress proxy)."""

    def __init__(self, http: httpx.AsyncClient, timeout_s: float = 15.0) -> None:
        self._http = http
        self._timeout = timeout_s

    async def _token(self, provider: OAuthProvider, form: dict[str, str]) -> TokenSet:
        try:
            r = await self._http.post(
                provider.token_url,
                data={
                    **form,
                    "client_id": provider.client_id,
                    "client_secret": provider.client_secret,
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning("OAuth %s không phản hồi: %s", provider.id, type(exc).__name__)
            raise OAuthError() from exc
        if r.status_code != 200:
            # Không đưa body ra ngoài (có thể chứa thông tin nhạy cảm); chỉ mã lỗi chuẩn.
            err = ""
            with contextlib.suppress(ValueError):
                err = str(r.json().get("error", ""))[:60]
            logger.warning("OAuth %s token lỗi HTTP %s %s", provider.id, r.status_code, err)
            raise OAuthError(f"Nhà cung cấp từ chối cấp token ({err or r.status_code}).")
        body = r.json()
        access = str(body.get("access_token") or "")
        if not access:
            raise OAuthError()
        return TokenSet(
            access_token=access,
            refresh_token=body.get("refresh_token"),
            expires_at=time.time() + float(body.get("expires_in") or 3600),
        )

    async def exchange(
        self, provider: OAuthProvider, code: str, code_verifier: str, redirect_uri: str
    ) -> TokenSet:
        return await self._token(
            provider,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )

    async def refresh(self, provider: OAuthProvider, refresh_token: str) -> TokenSet:
        return await self._token(
            provider, {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def userinfo(self, provider: OAuthProvider, access_token: str) -> dict[str, Any]:
        if not provider.userinfo_url:
            return {}
        try:
            r = await self._http.get(
                provider.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise OAuthError() from exc
        if r.status_code != 200:
            raise OAuthError()
        return cast("dict[str, Any]", r.json())


class RedisOAuthStateStore:
    _PREFIX = "aries:oauth:state:"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def put(self, state: str, data: dict[str, Any], ttl_s: int) -> None:
        await self._redis.set(self._PREFIX + state, json.dumps(data), ex=ttl_s)

    async def pop(self, state: str) -> dict[str, Any] | None:
        raw = await self._redis.getdel(self._PREFIX + state)  # một lần dùng, nguyên tử
        if raw is None:
            return None
        return cast("dict[str, Any]", json.loads(raw))
