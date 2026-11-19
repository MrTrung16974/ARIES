"""Xác thực access token OIDC (Keycloak) bằng JWKS.

- Chỉ chấp nhận thuật toán bất đối xứng trong allowlist (chặn `alg: none`/HS256 confusion).
- Kiểm `iss`, `aud`, `exp`, `iat`, `sub`; từ chối ID token (`typ` khác `Bearer`).
- Cache JWKS; `kid` lạ chỉ kích hoạt tải lại tối đa một lần mỗi `min_refresh_interval` giây
  để không bị lợi dụng làm DoS lên Keycloak.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

import httpx
import jwt

from aiagent.modules.identity.domain import Principal, Role
from aiagent.shared_kernel.errors import UnauthorizedError

logger = logging.getLogger(__name__)

DEFAULT_ALGORITHMS: tuple[str, ...] = ("RS256", "PS256", "ES256")
_KNOWN_ROLES = {r.value: r for r in Role}


class JwksTokenVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        http: httpx.AsyncClient,
        tenant_claim: str = "tenant_id",
        leeway_seconds: int = 30,
        cache_ttl_seconds: float = 300.0,
        min_refresh_interval_seconds: float = 30.0,
        algorithms: Iterable[str] = DEFAULT_ALGORITHMS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url
        self._http = http
        self._tenant_claim = tenant_claim
        self._leeway = leeway_seconds
        self._ttl = cache_ttl_seconds
        self._min_refresh = min_refresh_interval_seconds
        self._algorithms = frozenset(algorithms)
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at = float("-inf")
        self._lock = asyncio.Lock()

    async def verify(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise UnauthorizedError() from exc

        alg = header.get("alg")
        kid = header.get("kid")
        if alg not in self._algorithms or not isinstance(kid, str):
            raise UnauthorizedError()

        key = (await self._get_keys()).get(kid)
        if key is None:
            key = (await self._get_keys(force=True)).get(kid)
        if key is None:
            raise UnauthorizedError()

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key.key,
                algorithms=[alg],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "sub", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise UnauthorizedError() from exc

        if claims.get("typ", "Bearer") != "Bearer":
            raise UnauthorizedError()

        tenant_id = claims.get(self._tenant_claim)
        if not isinstance(tenant_id, str) or not tenant_id:
            raise UnauthorizedError()

        return Principal(
            subject=str(claims["sub"]),
            tenant_id=tenant_id,
            username=_opt_str(claims.get("preferred_username")),
            email=_opt_str(claims.get("email")),
            roles=_extract_roles(claims),
        )

    async def _get_keys(self, *, force: bool = False) -> dict[str, jwt.PyJWK]:
        if not self._is_stale(force):
            return self._keys
        async with self._lock:
            if not self._is_stale(force):  # một coroutine khác vừa tải xong
                return self._keys
            try:
                resp = await self._http.get(self._jwks_url, timeout=5.0)
                resp.raise_for_status()
                jwks = jwt.PyJWKSet.from_dict(resp.json())
            except (httpx.HTTPError, ValueError, jwt.PyJWTError) as exc:
                logger.error("Không tải được JWKS từ %s: %s", self._jwks_url, type(exc).__name__)
                if self._keys:
                    return self._keys
                raise UnauthorizedError() from exc
            self._keys = {k.key_id: k for k in jwks.keys if k.key_id}
            self._fetched_at = self._clock()
            return self._keys

    def _is_stale(self, force: bool) -> bool:
        age = self._clock() - self._fetched_at
        if force:
            return age >= self._min_refresh
        return not self._keys or age >= self._ttl


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _extract_roles(claims: dict[str, Any]) -> frozenset[Role]:
    realm_access = claims.get("realm_access")
    raw = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
    return frozenset(_KNOWN_ROLES[r] for r in raw if isinstance(r, str) and r in _KNOWN_ROLES)
