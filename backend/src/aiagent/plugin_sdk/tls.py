"""TLS dùng chung cho connector: CA riêng + chứng chỉ client (mTLS) dạng PEM — ADR-0023.

- `ca_cert`, `client_cert` là cấu hình (công khai); `client_key` là secret (Credential Broker).
- Thư viện chỉ nhận đường dẫn file (ssl.load_cert_chain, redis-py) → `pem_files` ghi ra thư mục tạm
  quyền 0600 và xóa ngay khi xong.
"""

from __future__ import annotations

import os
import ssl
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PEM_MEDIA_TYPE = "application/x-pem-file"
CLIENT_KEY_SECRET = "client_key"  # noqa: S105 - tên secret, không phải giá trị
MAX_PEM_CHARS = 20_000


def pem_property(title: str, description: str = "") -> dict[str, Any]:
    """Thuộc tính config_schema cho một khối PEM (UI hiện ô nhiều dòng)."""
    prop: dict[str, Any] = {
        "type": "string",
        "maxLength": MAX_PEM_CHARS,
        "contentMediaType": PEM_MEDIA_TYPE,
        "title": title,
    }
    if description:
        prop["description"] = description
    return prop


def tls_properties() -> dict[str, dict[str, Any]]:
    """`ca_cert` + `client_cert` cho config_schema; kèm secret `client_key` trong manifest."""
    return {
        "ca_cert": pem_property(
            "CA riêng (PEM, tùy chọn)", "Chứng chỉ CA nội bộ để xác thực máy chủ"
        ),
        "client_cert": pem_property(
            "Chứng chỉ client mTLS (PEM, tùy chọn)", "Khóa riêng nhập ở ô 'client_key'"
        ),
    }


class TlsConfigError(ValueError):
    """Cấu hình TLS sai — thông điệp an toàn (không chứa nội dung khóa)."""


def material(config: dict[str, Any], secrets: dict[str, str]) -> tuple[str, str, str]:
    """(ca, cert, key) đã kiểm cú pháp; chuỗi rỗng nếu không có."""
    ca = str(config.get("ca_cert") or "").strip()
    cert = str(config.get("client_cert") or "").strip()
    key = str(secrets.get(CLIENT_KEY_SECRET) or "").strip()
    for label, text in (("CA", ca), ("chứng chỉ client", cert), ("client_key", key)):
        if text and "-----BEGIN " not in text:
            raise TlsConfigError(f"{label} không phải khối PEM hợp lệ.")
    if bool(cert) != bool(key):
        raise TlsConfigError("mTLS cần cả chứng chỉ client và client_key.")
    return ca, cert, key


@contextmanager
def pem_files(cert: str, key: str) -> Iterator[tuple[str, str]]:
    with tempfile.TemporaryDirectory(prefix="aries-tls-") as folder:
        paths = []
        for name, text in (("cert.pem", cert), ("key.pem", key)):
            path = Path(folder) / name
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(text + "\n")
            paths.append(str(path))
        yield paths[0], paths[1]


def ssl_context(
    config: dict[str, Any], secrets: dict[str, str], *, verify: bool = True
) -> ssl.SSLContext:
    """SSLContext client: CA hệ thống (hoặc CA riêng), mTLS nếu có chứng chỉ client."""
    ca, cert, key = material(config, secrets)
    try:
        ctx = ssl.create_default_context(cadata=ca) if ca else ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if cert:
            with pem_files(cert, key) as (cert_path, key_path):
                ctx.load_cert_chain(cert_path, key_path)
    except ssl.SSLError as exc:
        raise TlsConfigError("Chứng chỉ hoặc khóa TLS không hợp lệ / không khớp nhau.") from exc
    return ctx


__all__ = [
    "CLIENT_KEY_SECRET",
    "PEM_MEDIA_TYPE",
    "TlsConfigError",
    "material",
    "pem_files",
    "pem_property",
    "ssl_context",
    "tls_properties",
]
