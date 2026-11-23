"""Kiểm tra đích mạng dùng chung cho plugin (web.fetch, connector) — ADR-0023.

Hai chính sách khác nhau:
- `is_public` (web.fetch): chỉ địa chỉ công khai trên Internet.
- `NetworkPolicy` (connector): IP riêng (10.x, 192.168.x…) là hợp lệ vì hệ thống của công ty nằm
  ở đó, nhưng KHÔNG được trỏ vào hạ tầng của chính ARIES (postgres, redis, openbao…), loopback,
  link-local (metadata cloud 169.254.169.254) hay multicast. Có thể giới hạn thêm theo dải mạng.

Giới hạn còn lại: DNS rebinding giữa lúc kiểm và lúc plugin kết nối (plugin kết nối bằng tên
máy). Phân vùng mạng Docker/firewall vẫn là lớp chặn chính; đây là lớp kiểm tra ở ứng dụng.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

Resolver = Callable[[str, int], Awaitable[list[str]]]
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class BlockedEndpointError(ValueError):
    """Đích mạng bị chặn — thông điệp an toàn để hiện cho người dùng/model."""


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return sorted({str(info[4][0]) for info in infos})


def ip_literal(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return None


def _addr(ip: str) -> IPAddress:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def is_public(ip: str) -> bool:
    addr = _addr(ip)
    return addr.is_global and not addr.is_multicast


def normalize_host(host: str) -> str:
    return host.strip().strip("[]").rstrip(".").lower()


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int


def parse_host_port(text: str, default_port: int) -> Endpoint:
    """`host`, `host:port`, `[v6]:port` → Endpoint. ValueError nếu sai cú pháp."""
    text = text.strip()
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    elif text.count(":") == 1:
        host, port = text.split(":")
    else:
        host, port = text, ""
    if not host or (port and not port.isdigit()):
        raise ValueError(f"Địa chỉ không hợp lệ: {text[:100]}")
    return Endpoint(host, int(port) if port else default_port)


@dataclass(frozen=True)
class NetworkPolicy:
    """Chính sách đích mạng cho connector. Core dựng từ cấu hình và gắn vào `ConnectionContext`."""

    # Tên máy dịch vụ nội bộ của ARIES; IP mà chúng phân giải ra cũng bị chặn.
    blocked_hosts: frozenset[str] = frozenset()
    # Rỗng = mọi IP (trừ nhóm luôn chặn). Có giá trị = IP đích phải nằm trong một dải.
    allowed_networks: tuple[IPNetwork, ...] = ()
    allow_loopback: bool = False  # chỉ bật khi chạy backend trực tiếp trên máy dev
    resolver: Resolver | None = field(default=system_resolver, compare=False, repr=False)

    @classmethod
    def build(
        cls,
        *,
        blocked_hosts: Iterable[str] = (),
        allowed_networks: Iterable[str] = (),
        allow_loopback: bool = False,
        resolver: Resolver | None = system_resolver,
    ) -> NetworkPolicy:
        return cls(
            blocked_hosts=frozenset(normalize_host(h) for h in blocked_hosts if h.strip()),
            allowed_networks=tuple(
                ipaddress.ip_network(n.strip(), strict=False) for n in allowed_networks if n.strip()
            ),
            allow_loopback=allow_loopback,
            resolver=resolver,
        )

    # ------------------------------------------------------------------ kiểm tra
    def _check_ip(self, ip: str) -> None:
        addr = _addr(ip)
        if addr.is_loopback:
            if self.allow_loopback:
                return
            raise BlockedEndpointError("Không được kết nối tới địa chỉ loopback.")
        if addr.is_unspecified or addr.is_multicast or addr.is_link_local or addr.is_reserved:
            raise BlockedEndpointError(f"Địa chỉ {ip} không được phép.")
        if self.allowed_networks and not any(addr in n for n in self.allowed_networks):
            raise BlockedEndpointError(f"Địa chỉ {ip} nằm ngoài các dải mạng được phép.")

    def check_syntax(self, endpoint: Endpoint) -> str:
        """Kiểm tra không cần DNS (lúc lưu cấu hình). Trả lại tên máy đã chuẩn hóa."""
        host = normalize_host(endpoint.host)
        if not host or len(host) > 253:
            raise BlockedEndpointError("Tên máy chủ không hợp lệ.")
        if not 1 <= endpoint.port <= 65535:
            raise BlockedEndpointError(f"Cổng {endpoint.port} không hợp lệ.")
        if host in self.blocked_hosts:
            raise BlockedEndpointError(f"'{host}' là dịch vụ nội bộ của ARIES — không được phép.")
        if not self.allow_loopback and (host == "localhost" or host.endswith(".localhost")):
            raise BlockedEndpointError("Không được kết nối tới địa chỉ loopback.")
        literal = ip_literal(host)
        if literal is not None:
            self._check_ip(literal)
        return host

    async def check(self, endpoint: Endpoint) -> list[str]:
        """Kiểm tra đầy đủ (lúc dùng): cú pháp + mọi IP phân giải ra. Trả về danh sách IP."""
        host = self.check_syntax(endpoint)
        literal = ip_literal(host)
        if literal is not None:
            ips = [literal]
        elif self.resolver is None:
            return []
        else:
            try:
                ips = await self.resolver(host, endpoint.port)
            except OSError as exc:
                raise BlockedEndpointError(f"Không phân giải được tên máy {host}.") from exc
            if not ips:
                raise BlockedEndpointError(f"Không phân giải được tên máy {host}.")
        for ip in ips:
            self._check_ip(ip)
        internal = await self._internal_ips()
        if any(_addr(ip) in internal for ip in ips):
            raise BlockedEndpointError(
                f"'{host}' trỏ vào dịch vụ nội bộ của ARIES — không được phép."
            )
        return ips

    async def _internal_ips(self) -> set[IPAddress]:
        if self.resolver is None or not self.blocked_hosts:
            return set()
        resolver = self.resolver

        async def one(host: str) -> list[str]:
            try:
                return await resolver(host, 0)
            except OSError:  # dịch vụ không chạy/không có trong DNS → không có IP để chặn
                return []

        found = await asyncio.gather(*(one(h) for h in sorted(self.blocked_hosts)))
        return {_addr(ip) for ips in found for ip in ips}


__all__ = [
    "BlockedEndpointError",
    "Endpoint",
    "NetworkPolicy",
    "Resolver",
    "ip_literal",
    "is_public",
    "normalize_host",
    "parse_host_port",
    "system_resolver",
]
