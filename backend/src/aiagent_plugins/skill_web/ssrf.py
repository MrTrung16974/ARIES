"""Chặn SSRF cho web.fetch: chỉ http(s), cổng web, và mọi IP phân giải ra phải là địa chỉ công khai.

Giới hạn còn lại (ADR-0016): DNS rebinding giữa lúc kiểm và lúc kết nối — Phase 3b đưa mọi
egress qua proxy (Squid) có allowlist để đóng hẳn.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
Resolver = Callable[[str, int], Awaitable[list[str]]]


class BlockedUrlError(ValueError):
    """URL bị chặn — thông điệp an toàn để trả về model."""


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return sorted({str(info[4][0]) for info in infos})


def _ip_literal(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return None


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


async def check_url(url: str, resolver: Resolver | None = system_resolver) -> str:
    """Trả lại URL đã chuẩn hóa nếu an toàn; ném BlockedUrlError nếu không.

    `resolver=None`: đi qua egress proxy — proxy tự phân giải và chặn IP nội bộ (ADR-0018),
    ở đây chỉ kiểm cú pháp, IP viết thẳng và tên máy nội bộ một nhãn (`postgres`, `openbao`…).
    """
    if len(url) > 2048:
        raise BlockedUrlError("URL quá dài.")
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        raise BlockedUrlError("Chỉ hỗ trợ http và https.")
    if parts.username or parts.password:
        raise BlockedUrlError("URL không được chứa thông tin đăng nhập.")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise BlockedUrlError("URL thiếu tên máy chủ.")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise BlockedUrlError("Không được truy cập địa chỉ nội bộ.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise BlockedUrlError("Cổng không hợp lệ.") from exc
    if port not in ALLOWED_PORTS:
        raise BlockedUrlError(f"Cổng {port} không được phép.")
    literal = _ip_literal(host)
    if literal is not None:
        if not _is_public(literal):
            raise BlockedUrlError("Không được truy cập địa chỉ nội bộ/riêng.")
        return parts.geturl()
    if "." not in host:
        raise BlockedUrlError("Không được truy cập máy nội bộ.")
    if resolver is None:
        return parts.geturl()
    try:
        ips = await resolver(host, port)
    except OSError as exc:
        raise BlockedUrlError(f"Không phân giải được tên miền {host}.") from exc
    if not ips:
        raise BlockedUrlError(f"Không phân giải được tên miền {host}.")
    if not all(_is_public(ip) for ip in ips):
        raise BlockedUrlError("Không được truy cập địa chỉ nội bộ/riêng.")
    return parts.geturl()
