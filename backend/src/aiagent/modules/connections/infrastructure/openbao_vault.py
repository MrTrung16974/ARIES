"""Kho bí mật OpenBao (KV v2) qua HTTP API — ADR-0008, ADR-0016, ADR-0018.

Xác thực: AppRole (role_id + secret_id từ file do `openbao-init` cấp) — token có TTL, tự đăng
nhập lại khi sắp hết hạn hoặc bị 403. Token tĩnh chỉ để dùng cho test/dev đặc biệt.
Không log giá trị secret, không đưa body phản hồi lỗi ra ngoài.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from aiagent.shared_kernel.errors import AppError

logger = logging.getLogger(__name__)

_PATH_RE = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*$")
_RENEW_MARGIN_S = 60


class VaultError(AppError):
    code = "vault_unavailable"
    status_code = 503
    public_message = "Kho bí mật không khả dụng."


@dataclass(frozen=True)
class AppRoleCredentials:
    role_id_file: Path
    secret_id_file: Path

    def read(self) -> tuple[str, str]:
        # Đọc mỗi lần đăng nhập: openbao-init cấp secret_id mới mỗi lần khởi động.
        return (
            self.role_id_file.read_text(encoding="utf-8").strip(),
            self.secret_id_file.read_text(encoding="utf-8").strip(),
        )


class OpenBaoVault:
    def __init__(
        self,
        addr: str,
        http: httpx.AsyncClient,
        *,
        token: str | None = None,
        approle: AppRoleCredentials | None = None,
        mount: str = "secret",
        timeout_s: float = 5.0,
    ) -> None:
        if not token and approle is None:
            raise ValueError("Cần token hoặc AppRole")
        self._addr = addr.rstrip("/")
        self._http = http
        self._static_token = token
        self._approle = approle
        self._mount = mount
        self._timeout = timeout_s
        self._token: str | None = token
        self._expires_at = float("inf") if token else 0.0
        self._lock = asyncio.Lock()

    def _url(self, kind: str, path: str) -> str:
        if not _PATH_RE.match(path):
            raise ValueError("Đường dẫn secret không hợp lệ")
        return f"{self._addr}/v1/{self._mount}/{kind}/{path}"

    # ------------------------------------------------------------------ xác thực
    async def _login(self) -> str:
        if self._approle is None:
            raise VaultError()
        try:
            role_id, secret_id = self._approle.read()
        except OSError as exc:
            logger.error("Không đọc được thông tin AppRole của OpenBao")
            raise VaultError() from exc
        try:
            r = await self._http.post(
                f"{self._addr}/v1/auth/approle/login",
                json={"role_id": role_id, "secret_id": secret_id},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning("OpenBao không phản hồi khi đăng nhập: %s", type(exc).__name__)
            raise VaultError() from exc
        if r.status_code != 200:
            logger.error("Đăng nhập AppRole OpenBao lỗi HTTP %s", r.status_code)
            raise VaultError()
        auth = r.json().get("auth") or {}
        token = str(auth.get("client_token") or "")
        if not token:
            raise VaultError()
        lease = float(auth.get("lease_duration") or 300)
        self._token = token
        self._expires_at = time.monotonic() + max(30.0, lease - _RENEW_MARGIN_S)
        return token

    async def _current_token(self, *, force: bool = False) -> str:
        if self._static_token:
            return self._static_token
        async with self._lock:
            if force or self._token is None or time.monotonic() >= self._expires_at:
                return await self._login()
            return self._token

    async def _call(self, method: str, url: str, **kw: object) -> httpx.Response:
        for attempt in range(2):
            token = await self._current_token(force=attempt > 0)
            try:
                r = await self._http.request(
                    method,
                    url,
                    headers={"X-Vault-Token": token},
                    timeout=self._timeout,
                    **kw,  # type: ignore[arg-type]
                )
            except httpx.HTTPError as exc:
                logger.warning("OpenBao không phản hồi: %s", type(exc).__name__)
                raise VaultError() from exc
            # Token hết hạn/bị thu hồi → đăng nhập lại một lần (chỉ với AppRole).
            if r.status_code == 403 and attempt == 0 and not self._static_token:
                continue
            return r
        return r

    # ------------------------------------------------------------------ KV v2
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
        """Sẵn sàng = đã mở khóa và đang active."""
        try:
            r = await self._http.get(f"{self._addr}/v1/sys/health", timeout=self._timeout)
        except httpx.HTTPError:
            return False
        return r.status_code == 200


class UnconfiguredVault:
    """Dùng khi chưa cấu hình OpenBao: mọi thao tác secret báo lỗi rõ ràng."""

    async def put(self, path: str, data: dict[str, str]) -> None:
        raise VaultError("Chưa cấu hình kho bí mật OpenBao.")

    async def get(self, path: str) -> dict[str, str]:
        raise VaultError("Chưa cấu hình kho bí mật OpenBao.")

    async def delete(self, path: str) -> None:
        return None
