from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from aiagent.modules.connections.infrastructure.openbao_vault import (
    AppRoleCredentials,
    OpenBaoVault,
    VaultError,
)


def _creds(tmp_path: Path) -> AppRoleCredentials:
    (tmp_path / "role_id").write_text("role-1\n", encoding="utf-8")
    (tmp_path / "secret_id").write_text("secret-1\n", encoding="utf-8")
    return AppRoleCredentials(tmp_path / "role_id", tmp_path / "secret_id")


async def test_approle_login_and_relogin_on_403(tmp_path: Path) -> None:
    logins: list[dict] = []  # type: ignore[type-arg]
    tokens = iter(["tok-1", "tok-2"])
    store: dict[str, dict[str, str]] = {}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/auth/approle/login":
            logins.append(dict(__import__("json").loads(request.content)))
            return httpx.Response(
                200, json={"auth": {"client_token": next(tokens), "lease_duration": 3600}}
            )
        token = request.headers.get("X-Vault-Token")
        if token == "tok-1" and request.method == "GET":
            return httpx.Response(403)  # token bị thu hồi
        path = request.url.path.removeprefix("/v1/secret/data/")
        if request.method == "POST":
            store[path] = __import__("json").loads(request.content)["data"]
            return httpx.Response(204)
        if path in store:
            return httpx.Response(200, json={"data": {"data": store[path]}})
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    vault = OpenBaoVault("http://bao:8200", http, approle=_creds(tmp_path))
    await vault.put("aries/t/connections/1", {"password": "x"})
    assert await vault.get("aries/t/connections/1") == {"password": "x"}
    assert logins == [
        {"role_id": "role-1", "secret_id": "secret-1"},
        {"role_id": "role-1", "secret_id": "secret-1"},
    ]


async def test_approle_missing_files_and_bad_login(tmp_path: Path) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400)))
    missing = OpenBaoVault(
        "http://bao:8200",
        http,
        approle=AppRoleCredentials(tmp_path / "nope", tmp_path / "nope2"),
    )
    with pytest.raises(VaultError):
        await missing.get("aries/t/x")
    bad = OpenBaoVault("http://bao:8200", http, approle=_creds(tmp_path))
    with pytest.raises(VaultError):
        await bad.get("aries/t/x")
    with pytest.raises(ValueError):
        OpenBaoVault("http://bao:8200", http)
