from __future__ import annotations

import httpx
import jwt
import pytest

from aiagent.modules.identity.domain import Role
from aiagent.modules.identity.infrastructure.jwks_verifier import JwksTokenVerifier
from aiagent.shared_kernel.errors import UnauthorizedError
from tests.conftest import AUDIENCE, ISSUER, JWKS_URL, KeyPair


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def verifier(jwks_transport, clock) -> JwksTokenVerifier:  # type: ignore[no-untyped-def]
    transport, _ = jwks_transport
    return JwksTokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=JWKS_URL,
        http=httpx.AsyncClient(transport=transport),
        clock=clock,
    )


async def test_valid_token_returns_principal(verifier, make_token) -> None:  # type: ignore[no-untyped-def]
    principal = await verifier.verify(make_token(roles=["builder", "unknown-role"]))
    assert principal.subject == "user-1"
    assert principal.tenant_id == "default"
    assert principal.username == "alice"
    assert principal.roles == frozenset({Role.BUILDER})


async def test_audience_as_list_is_accepted(verifier, make_token) -> None:  # type: ignore[no-untyped-def]
    principal = await verifier.verify(make_token(aud=["account", AUDIENCE]))
    assert principal.subject == "user-1"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"aud": "other-api"},
        {"iss": "https://evil.test/realms/aiagent"},
        {"exp_in": -3600},
        {"tenant_id": None},
        {"tenant_id": ""},
        {"extra": {"typ": "ID"}},
    ],
    ids=["wrong-aud", "wrong-iss", "expired", "no-tenant", "empty-tenant", "id-token"],
)
async def test_invalid_claims_are_rejected(verifier, make_token, kwargs) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(UnauthorizedError):
        await verifier.verify(make_token(**kwargs))


async def test_token_signed_by_unknown_key_is_rejected(verifier, make_token) -> None:  # type: ignore[no-untyped-def]
    attacker = KeyPair("test-key-1")  # cùng kid, khác khóa
    with pytest.raises(UnauthorizedError):
        await verifier.verify(make_token(key=attacker))


async def test_alg_none_and_hs256_are_rejected(verifier) -> None:  # type: ignore[no-untyped-def]
    claims = {
        "sub": "x",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": 9999999999,
        "iat": 0,
        "tenant_id": "t",
    }
    unsigned = jwt.encode(claims, key=None, algorithm="none", headers={"kid": "test-key-1"})
    hs = jwt.encode(
        claims,
        key="secret-secret-secret-secret-1234",
        algorithm="HS256",
        headers={"kid": "test-key-1"},
    )
    for token in (unsigned, hs, "not-a-jwt"):
        with pytest.raises(UnauthorizedError):
            await verifier.verify(token)


async def test_jwks_is_cached(verifier, make_token, jwks_transport) -> None:  # type: ignore[no-untyped-def]
    _, counter = jwks_transport
    for _ in range(5):
        await verifier.verify(make_token())
    assert counter["calls"] == 1


async def test_unknown_kid_refresh_is_rate_limited(
    verifier, make_token, jwks_transport, clock
) -> None:  # type: ignore[no-untyped-def]
    _, counter = jwks_transport
    await verifier.verify(make_token())  # lần tải đầu
    for _ in range(10):
        with pytest.raises(UnauthorizedError):
            await verifier.verify(make_token(kid="rotated-key"))
    assert counter["calls"] == 1  # chưa qua min_refresh_interval

    clock.now += 31
    with pytest.raises(UnauthorizedError):
        await verifier.verify(make_token(kid="rotated-key"))
    assert counter["calls"] == 2
