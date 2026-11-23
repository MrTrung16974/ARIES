"""Connector Redis (ADR-0023).

- Mỗi lần gọi tool mở một kết nối ngắn (ACL user + mật khẩu do Credential Broker cấp, TLS tùy chọn).
- Phạm vi theo `key_prefixes`: mọi key/pattern phải bắt đầu bằng một tiền tố được phép.
- Đọc: `redis.scan`, `redis.get` (theo kiểu dữ liệu, giới hạn số phần tử và độ dài giá trị).
- Ghi (`redis.set`, `redis.delete`): chỉ khi `allow_write`; delete là destructive → luôn cần duyệt.
- Không có lệnh tùy ý (EVAL, CONFIG, FLUSH…). Không hỗ trợ Redis Cluster/Sentinel.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.cluster import ClusterNode, RedisCluster
from redis.asyncio.sentinel import Sentinel
from redis.exceptions import AuthenticationError, RedisError, ResponseError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    BlockedEndpointError,
    ConnectionContext,
    Endpoint,
    HealthStatus,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolNotFoundError,
    ToolResult,
    ToolSpec,
)
from aiagent.plugin_sdk.net import parse_host_port
from aiagent.plugin_sdk.tls import (
    CLIENT_KEY_SECRET,
    TlsConfigError,
    material,
    pem_files,
    tls_properties,
)

MAX_VALUE_CHARS = 2000
_GLOB_CHARS = "*?[\\"

_KEY_ARG = {"type": "string", "minLength": 1, "maxLength": 512}

_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["standalone", "cluster", "sentinel"],
            "default": "standalone",
            "title": "Chế độ",
        },
        "host": {
            "type": "string",
            "minLength": 1,
            "maxLength": 253,
            "title": "Máy chủ (standalone)",
        },
        "nodes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 260},
            "minItems": 1,
            "maxItems": 20,
            "title": "Node khởi tạo (cluster)",
            "description": "host:port, ví dụ redis-1.congty.vn:6379",
        },
        "sentinels": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 260},
            "minItems": 1,
            "maxItems": 10,
            "title": "Sentinel (sentinel)",
            "description": "host:port, ví dụ sentinel-1.congty.vn:26379",
        },
        "service_name": {
            "type": "string",
            "pattern": r"^[A-Za-z0-9_.\-]{1,100}$",
            "title": "Tên master (sentinel)",
        },
        "port": {"type": "integer", "minimum": 1, "maximum": 65535, "default": 6379},
        "db": {"type": "integer", "minimum": 0, "maximum": 255, "default": 0, "title": "Database"},
        "username": {"type": "string", "maxLength": 200, "title": "Người dùng (ACL, tùy chọn)"},
        "tls": {"type": "boolean", "default": False, "title": "Dùng TLS"},
        **tls_properties(),
        "key_prefixes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 200},
            "minItems": 1,
            "maxItems": 50,
            "title": "Tiền tố key được phép",
            "description": "Ví dụ cache:, session: — bắt buộc để giới hạn phạm vi",
        },
        "max_items": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 60, "default": 10},
        "allow_write": {"type": "boolean", "default": False, "title": "Cho phép ghi (cần duyệt)"},
    },
    "required": ["key_prefixes"],
    "allOf": [
        {
            "if": {"properties": {"mode": {"const": "cluster"}}, "required": ["mode"]},
            "then": {"required": ["nodes"]},
        },
        {
            "if": {"properties": {"mode": {"const": "sentinel"}}, "required": ["mode"]},
            "then": {"required": ["sentinels", "service_name"]},
        },
        {
            "if": {
                "properties": {"mode": {"enum": ["cluster", "sentinel"]}},
                "required": ["mode"],
            },
            "else": {"required": ["host"]},
        },
    ],
    "additionalProperties": False,
}

_MANIFEST = PluginManifest(
    id="redis",
    kind=PluginKind.CONNECTOR,
    name="Redis",
    description="Đọc key Redis trong phạm vi tiền tố cho phép (string, hash, list, set, zset, stream).",
    version="1.0.0",
    api_version=PLUGIN_API_VERSION,
    config_schema=_CONFIG_SCHEMA,
    secrets=("password", "sentinel_password", CLIENT_KEY_SECRET),
    optional_secrets=("password", "sentinel_password", CLIENT_KEY_SECRET),
    tools=(
        ToolSpec(
            name="redis.scan",
            description=(
                "Liệt kê key theo pattern glob (vd. 'cache:user:*'); pattern phải bắt đầu bằng "
                "một tiền tố được phép. Bỏ trống = mọi key trong phạm vi."
            ),
            risk=RiskLevel.READ,
            untrusted_output=True,
            input_schema={
                "type": "object",
                "properties": {"pattern": {"type": "string", "maxLength": 512}},
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="redis.get",
            description="Đọc một key: kiểu, TTL và giá trị (tự xử lý string/hash/list/set/zset/stream).",
            risk=RiskLevel.READ,
            untrusted_output=True,
            input_schema={
                "type": "object",
                "properties": {"key": _KEY_ARG},
                "required": ["key"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="redis.set",
            description="Ghi giá trị string cho một key (tùy chọn TTL giây). Cần duyệt.",
            risk=RiskLevel.WRITE,
            input_schema={
                "type": "object",
                "properties": {
                    "key": _KEY_ARG,
                    "value": {"type": "string", "maxLength": 100_000},
                    "ttl_s": {"type": "integer", "minimum": 1, "maximum": 31_536_000},
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="redis.delete",
            description="Xóa một key. Chỉ dùng khi người dùng yêu cầu rõ ràng; luôn cần duyệt.",
            risk=RiskLevel.DESTRUCTIVE,
            input_schema={
                "type": "object",
                "properties": {"key": _KEY_ARG},
                "required": ["key"],
                "additionalProperties": False,
            },
        ),
    ),
    ui_hints={"icon": "database", "category": "data", "multiline_secrets": [CLIENT_KEY_SECRET]},
)


class KeyRejectedError(ValueError):
    pass


def _prefixes(cfg: dict[str, Any]) -> list[str]:
    return [str(p) for p in cfg.get("key_prefixes") or [] if str(p)]


def check_key(key: str, prefixes: list[str]) -> str:
    if not any(key.startswith(p) for p in prefixes):
        raise KeyRejectedError(f"Key '{key[:100]}' nằm ngoài các tiền tố được phép.")
    return key


def scan_patterns(pattern: str, prefixes: list[str]) -> list[str]:
    """Pattern trống → quét từng tiền tố; có pattern → phần chữ trước ký tự glob phải ở trong phạm vi."""
    if not pattern:
        return [_escape(p) + "*" for p in prefixes]
    literal = pattern
    for i, ch in enumerate(pattern):
        if ch in _GLOB_CHARS:
            literal = pattern[:i]
            break
    if not any(literal.startswith(p) for p in prefixes):
        raise KeyRejectedError("Pattern phải bắt đầu bằng một tiền tố được phép (vd. 'cache:*').")
    return [pattern]


def _escape(text: str) -> str:
    return "".join("\\" + c if c in _GLOB_CHARS else c for c in text)


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    text = str(value)
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "…"


# Test: (connection, tham số TLS) → client (fakeredis). Mặc định: `open_client` theo chế độ.
ClientFactory = Callable[[ConnectionContext, dict[str, Any]], Any]
Closer = Callable[[], Awaitable[None]]


def _common(conn: ConnectionContext, tls: dict[str, Any]) -> dict[str, Any]:
    cfg = conn.config
    timeout = float(cfg.get("timeout_s", 10))
    return {
        "username": str(cfg["username"]) if cfg.get("username") else None,
        "password": conn.secrets.get("password") or None,
        "ssl": bool(cfg.get("tls", False)),
        "socket_timeout": timeout,
        "socket_connect_timeout": timeout,
        "client_name": "aries-agent",
        **tls,
    }


async def open_client(conn: ConnectionContext, tls: dict[str, Any]) -> tuple[Any, list[Closer]]:
    """Client theo chế độ; kiểm cả node do hệ thống quảng bá (master từ sentinel, node của cluster)
    theo chính sách mạng trước khi chạy lệnh."""
    cfg = conn.config
    mode = str(cfg.get("mode", "standalone"))
    common = _common(conn, tls)
    if mode == "cluster":
        nodes = [parse_host_port(str(n), 6379) for n in cfg.get("nodes") or []]
        cluster = RedisCluster(startup_nodes=[ClusterNode(e.host, e.port) for e in nodes], **common)
        try:
            await cluster.initialize()
            if conn.network is not None:
                for node in cluster.get_nodes():
                    await conn.network.check(Endpoint(node.host, int(node.port)))
        except BaseException:
            await cluster.aclose()
            raise
        return cluster, [cluster.aclose]
    if mode == "sentinel":
        points = [parse_host_port(str(n), 26379) for n in cfg.get("sentinels") or []]
        sentinel_kwargs = {
            "password": conn.secrets.get("sentinel_password") or None,
            "socket_timeout": common["socket_timeout"],
            "socket_connect_timeout": common["socket_connect_timeout"],
            "ssl": common["ssl"],
            **tls,
        }
        sentinel = Sentinel(  # type: ignore[no-untyped-call]  # redis-py chưa khai kiểu
            [(e.host, e.port) for e in points], sentinel_kwargs=sentinel_kwargs, **common
        )
        closers: list[Closer] = [s.aclose for s in sentinel.sentinels]
        try:
            service = str(cfg["service_name"])
            host, port = await sentinel.discover_master(service)
            if conn.network is not None:
                await conn.network.check(Endpoint(str(host), int(port)))
        except BaseException:
            for close in closers:
                await close()
            raise
        master = sentinel.master_for(service, db=int(cfg.get("db", 0)))
        return master, [master.aclose, *closers]
    client = Redis(
        host=str(cfg["host"]),
        port=int(cfg.get("port", 6379)),
        db=int(cfg.get("db", 0)),
        **common,
    )
    return client, [client.aclose]


class RedisConnector(Plugin):
    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self._factory = client_factory  # test truyền fakeredis

    @property
    def manifest(self) -> PluginManifest:
        return _MANIFEST

    def network_targets(self, config: dict[str, Any]) -> list[Endpoint]:
        mode = config.get("mode", "standalone")
        if mode == "cluster":
            return [parse_host_port(str(n), 6379) for n in config.get("nodes") or []]
        if mode == "sentinel":
            return [parse_host_port(str(n), 26379) for n in config.get("sentinels") or []]
        host = config.get("host")
        return [Endpoint(str(host), int(config.get("port", 6379)))] if host else []

    async def _open(self, conn: ConnectionContext, tls: dict[str, Any]) -> tuple[Any, list[Closer]]:
        if self._factory is not None:
            client = self._factory(conn, tls)
            return client, [client.aclose]
        return await open_client(conn, tls)

    @asynccontextmanager
    async def _client(self, conn: ConnectionContext) -> AsyncIterator[Any]:
        ca, cert, key = material(conn.config, conn.secrets)
        tls: dict[str, Any] = {}
        if conn.config.get("tls", False) and ca:
            tls["ssl_ca_data"] = ca
        if conn.config.get("tls", False) and cert:
            # redis-py chỉ nhận file → file tạm 0600, xóa ngay sau lượt gọi.
            with pem_files(cert, key) as (cert_path, key_path):
                client, closers = await self._open(
                    conn, {**tls, "ssl_certfile": cert_path, "ssl_keyfile": key_path}
                )
                try:
                    yield client
                finally:
                    for close in closers:
                        await close()
            return
        client, closers = await self._open(conn, tls)
        try:
            yield client
        finally:
            for close in closers:
                await close()

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        conn = ctx.connection
        if conn is None:
            return ToolResult("Tool này cần một kết nối Redis.", is_error=True)
        prefixes = _prefixes(conn.config)
        if not prefixes:
            return ToolResult("Kết nối chưa khai báo tiền tố key được phép.", is_error=True)
        try:
            if name == "redis.scan":
                return await self._scan(
                    conn, scan_patterns(str(arguments.get("pattern", "")), prefixes)
                )
            key = check_key(str(arguments.get("key", "")), prefixes)
            if name == "redis.get":
                return await self._get(conn, key)
            if name in ("redis.set", "redis.delete"):
                if not conn.config.get("allow_write", False):
                    return ToolResult("Kết nối này chỉ đọc (allow_write = false).", is_error=True)
                async with self._client(conn) as r:
                    if name == "redis.set":
                        ttl = arguments.get("ttl_s")
                        await r.set(key, str(arguments["value"]), ex=int(ttl) if ttl else None)
                        return ToolResult(f"Đã ghi key {key}.")
                    deleted = await r.delete(key)
                    return ToolResult(f"Đã xóa {deleted} key.")
        except (KeyRejectedError, TlsConfigError, BlockedEndpointError) as exc:
            return ToolResult(str(exc), is_error=True)
        except AuthenticationError:
            return ToolResult("Sai thông tin đăng nhập Redis.", is_error=True)
        except (RedisConnectionError, RedisTimeoutError, OSError):
            return ToolResult("Không kết nối được tới Redis.", is_error=True)
        except ResponseError as exc:
            return ToolResult(f"Redis từ chối: {str(exc)[:200]}", is_error=True)
        except RedisError as exc:
            return ToolResult(f"Lỗi Redis: {type(exc).__name__}", is_error=True)
        raise ToolNotFoundError(name)

    async def _scan(self, conn: ConnectionContext, patterns: list[str]) -> ToolResult:
        prefixes = _prefixes(conn.config)
        limit = int(conn.config.get("max_items", 100))
        keys: list[str] = []
        truncated = False
        async with self._client(conn) as r:
            try:  # scan_iter quét mọi node primary khi là cluster
                async with asyncio.timeout(float(conn.config.get("timeout_s", 10))):
                    for pattern in patterns:
                        async for raw in r.scan_iter(match=pattern, count=200):
                            key = (
                                raw.decode("utf-8", "replace")
                                if isinstance(raw, bytes)
                                else str(raw)
                            )
                            if any(key.startswith(p) for p in prefixes) and key not in keys:
                                keys.append(key)
                            if len(keys) >= limit:
                                truncated = True
                                break
                        if truncated:
                            break
            except TimeoutError:
                truncated = True  # keyspace lớn: trả phần đã quét được
        keys = sorted(keys[:limit])
        payload = {"keys": keys, "count": len(keys), "truncated": truncated}
        return ToolResult(json.dumps(payload, ensure_ascii=False), data={"count": len(keys)})

    async def _get(self, conn: ConnectionContext, key: str) -> ToolResult:
        n = int(conn.config.get("max_items", 100))
        async with self._client(conn) as r:
            kind = _text(await r.type(key))
            if kind == "none":
                return ToolResult(f"Key {key} không tồn tại.", is_error=True)
            ttl = await r.ttl(key)
            value: Any
            size: int | None = None
            if kind == "string":
                value = _text(await r.get(key))
            elif kind == "hash":
                size = await r.hlen(key)
                items: dict[str, str] = {}
                async for field, val in r.hscan_iter(key, count=200):
                    items[_text(field)] = _text(val)
                    if len(items) >= n:
                        break
                value = items
            elif kind == "list":
                size = await r.llen(key)
                value = [_text(v) for v in await r.lrange(key, 0, n - 1)]
            elif kind == "set":
                size = await r.scard(key)
                members: list[str] = []
                async for m in r.sscan_iter(key, count=200):
                    members.append(_text(m))
                    if len(members) >= n:
                        break
                value = sorted(members)
            elif kind == "zset":
                size = await r.zcard(key)
                value = [[_text(m), s] for m, s in await r.zrange(key, 0, n - 1, withscores=True)]
            elif kind == "stream":
                size = await r.xlen(key)
                value = [
                    {"id": _text(i), "fields": {_text(k): _text(v) for k, v in f.items()}}
                    for i, f in await r.xrevrange(key, count=n)
                ]
            else:
                value = f"(kiểu {kind} chưa hỗ trợ đọc)"
        payload: dict[str, Any] = {"key": key, "type": kind, "ttl_s": ttl, "value": value}
        if size is not None:
            payload["size"] = size
            payload["truncated"] = size > n
        return ToolResult(json.dumps(payload, ensure_ascii=False, default=str))

    async def test_connection(self, connection: ConnectionContext) -> HealthStatus:
        if not _prefixes(connection.config):
            return HealthStatus(False, "Chưa khai báo tiền tố key được phép.")
        try:
            async with self._client(connection) as r:
                await r.ping()
                try:  # user ACL hạn chế thường không có quyền INFO — không coi là lỗi kết nối
                    info: Any = await r.info("server")
                except ResponseError:
                    info = {}
        except (TlsConfigError, BlockedEndpointError) as exc:
            return HealthStatus(False, str(exc))
        except AuthenticationError:
            return HealthStatus(False, "Sai tên đăng nhập hoặc mật khẩu Redis.")
        except (RedisConnectionError, RedisTimeoutError, OSError):
            return HealthStatus(False, "Không kết nối được tới máy chủ Redis.")
        except RedisError as exc:
            return HealthStatus(False, f"Lỗi Redis: {str(exc)[:200]}")
        version = info.get("redis_version", "?") if isinstance(info, dict) else "?"
        return HealthStatus(True, f"Kết nối OK — Redis {version}.")
