"""Kho bí mật OpenBao (KV v2) qua HTTP API — ADR-0008, ADR-0016.

Không log giá trị secret, không đưa body phản hồi lỗi ra ngoài.
"""

from __future__ import annotations

import logging
import re

import httpx

from aiagent.shared_kernel.errors import AppError

logger = logging.getLogger(__name__)

_PATH_RE = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*$")


class VaultError(AppError):
    code = "vault_unavailable"
    status_code = 503
    public_message = "Kho bí mật không khả dụng."


class OpenBaoVault:
    def __init__(
        self,
        addr: str,
        token: str,
        http: httpx.AsyncClient,
        *,
        mount: str = "secret",
        timeout_s: float = 5.0,
    ) -> None:
        self._addr = addr.rstrip("/")
        self._token = token
        self._http = http
        self._mount = mount
        self._timeout = timeout_s

    def _url(self, kind: str, path: str) -> str:
        if not _PATH_RE.match(path):
            raise ValueError("Đường dẫn secret không hợp lệ")
        return f"{self._addr}/v1/{self._mount}/{kind}/{path}"

    async def _call(self, method: str, url: str, **kw: object) -> httpx.Response:
        try:
            return await self._http.request(
                method,
                url,
                headers={"X-Vault-Token": self._token},
                timeout=self._timeout,
                **kw,  # type: ignore[arg-type]
            )
        except httpx.HTTPError as exc:
            logger.warning("OpenBao không phản hồi: %s", type(exc).__name__)
            raise VaultError() from exc

    async def put(self, path: str, data: dict[str, str]) -> None:
        r = await self._call("POST", self._url("data", path), json={"data": data})
        if r.status_code not in (200, 204):
            logger.error("OpenBao ghi secret lỗi HTTP %s", r.status_code)
            raise VaultError()

    async def get(self, path: str) -> dict[str, str]:
        r = await self._call("GET", self._url("data", path))
        if r.status_code == 404:
            return {}
        if r.status_code != 200:
            logger.error("OpenBao đọc secret lỗi HTTP %s", r.status_code)
            raise VaultError()
        data = (r.json().get("data") or {}).get("data") or {}
        return {str(k): str(v) for k, v in data.items()}

    async def delete(self, path: str) -> None:
        # metadata DELETE xóa mọi version (không thể khôi phục).
        r = await self._call("DELETE", self._url("metadata", path))
        if r.status_code not in (200, 204, 404):
            logger.error("OpenBao xóa secret lỗi HTTP %s", r.status_code)
            raise VaultError()

    async def ping(self) -> bool:
        try:
            r = await self._http.get(f"{self._addr}/v1/sys/health", timeout=self._timeout)
        except httpx.HTTPError:
            return False
        return r.status_code == 200


class UnconfiguredVault:
    """Dùng khi chưa cấu hình OpenBao: mọi thao tác secret báo lỗi rõ ràng."""

    async def put(self, path: str, data: dict[str, str]) -> None:
        raise VaultError("Chưa cấu hình kho bí mật (AIAGENT_OPENBAO_TOKEN).")

    async def get(self, path: str) -> dict[str, str]:
        raise VaultError("Chưa cấu hình kho bí mật (AIAGENT_OPENBAO_TOKEN).")

    async def delete(self, path: str) -> None:
        return None
