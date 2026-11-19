from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.test/realms/aiagent"
AUDIENCE = "aiagent-api"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"


class KeyPair:
    def __init__(self, kid: str) -> None:
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def jwk(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key())
        )
        data.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return data


@pytest.fixture(scope="session")
def keypair() -> KeyPair:
    return KeyPair("test-key-1")


TokenFactory = Callable[..., str]


@pytest.fixture
def make_token(keypair: KeyPair) -> TokenFactory:
    def _make(
        *,
        sub: str = "user-1",
        tenant_id: str | None = "default",
        roles: list[str] | None = None,
        aud: str | list[str] = AUDIENCE,
        iss: str = ISSUER,
        exp_in: int = 300,
        kid: str | None = None,
        key: KeyPair | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "sub": sub,
            "iss": iss,
            "aud": aud,
            "iat": now,
            "exp": now + exp_in,
            "typ": "Bearer",
            "preferred_username": "alice",
            "email": "alice@example.test",
            "realm_access": {"roles": roles if roles is not None else ["user"]},
        }
        if tenant_id is not None:
            claims["tenant_id"] = tenant_id
        claims.update(extra or {})
        signer = key or keypair
        return jwt.encode(
            claims, signer.private, algorithm="RS256", headers={"kid": kid or signer.kid}
        )

    return _make


@pytest.fixture
def jwks_transport(keypair: KeyPair) -> tuple[httpx.MockTransport, dict[str, int]]:
    """MockTransport trả JWKS; đếm số lần bị gọi."""
    counter = {"calls": 0}
    keys = [keypair.jwk()]

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JWKS_URL
        counter["calls"] += 1
        return httpx.Response(200, json={"keys": keys})

    return httpx.MockTransport(handler), counter
