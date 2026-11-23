"""Connector gRPC (ADR-0023).

- Khám phá qua **server reflection**: mỗi method (unary và streaming) của service được phép thành
  một tool; client-streaming/bidi nhận mảng `requests`, server-streaming thu tối đa N message;
  input schema sinh từ protobuf (ánh xạ JSON chuẩn của proto3), gọi bằng JSON ↔ protobuf.
- Rủi ro mặc định theo tên method: Get/List/Search/Find/Read/Describe/Check/Query/Count/Lookup →
  read; còn lại → write. Admin chỉnh bằng `risk_overrides` của connection.
- TLS (CA hệ thống) hoặc plaintext; xác thực bằng metadata (Bearer hoặc header tùy chọn).
- Không qua HTTP proxy (`grpc.enable_http_proxy=0`). Chưa hỗ trợ streaming, mTLS, descriptor tải lên.
- Client gRPC đồng bộ chạy trong thread (reflection client của grpcio là đồng bộ).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import grpc
from google.protobuf import descriptor_pb2, descriptor_pool, json_format, message_factory
from google.protobuf.descriptor import Descriptor, FieldDescriptor, MethodDescriptor
from grpc_reflection.v1alpha.proto_reflection_descriptor_database import (
    ProtoReflectionDescriptorDatabase,
)

from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    ConnectionContext,
    Endpoint,
    HealthStatus,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from aiagent.plugin_sdk.net import parse_host_port
from aiagent.plugin_sdk.tls import CLIENT_KEY_SECRET, TlsConfigError, material, tls_properties

PLUGIN_ID = "grpc"
MAX_SLUG = 40
MAX_DEPTH = 5
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 12_000
CACHE_TTL_S = 300.0
MAX_PROTO_CHARS = 200_000
PROTOC_TIMEOUT_S = 30
_HIDDEN_SERVICES = ("grpc.reflection.", "grpc.health.", "grpc.channelz.")
_READ_PREFIXES = (
    "get",
    "list",
    "search",
    "find",
    "read",
    "describe",
    "check",
    "query",
    "count",
    "lookup",
    "fetch",
    "watch",
    "stream",
    "subscribe",
    "tail",
)
MAX_STREAM_REQUESTS = 100

_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "minLength": 3,
            "maxLength": 260,
            "title": "Địa chỉ máy chủ (host:port)",
            "description": "Ví dụ billing.congty.vn:443",
        },
        "tls": {"type": "boolean", "default": True, "title": "Dùng TLS"},
        **tls_properties(),
        "auth_type": {
            "type": "string",
            "enum": ["none", "bearer", "metadata"],
            "default": "none",
            "title": "Kiểu xác thực",
            "description": "Token nhập ở ô 'credential'",
        },
        "auth_name": {
            "type": "string",
            "pattern": r"^[a-z0-9_\-]{1,100}$",
            "default": "x-api-key",
            "title": "Tên metadata chứa key (kiểu metadata)",
        },
        "allowed_services": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^[A-Za-z0-9_.]+\*?$", "maxLength": 200},
            "maxItems": 50,
            "default": [],
            "title": "Service được phép (trống = tất cả)",
            "description": "Tên đầy đủ, ví dụ billing.v1.InvoiceService hoặc billing.*",
        },
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 120, "default": 15},
        "max_methods": {"type": "integer", "minimum": 1, "maximum": 150, "default": 60},
        "max_stream_messages": {
            "type": "integer",
            "minimum": 1,
            "maximum": 500,
            "default": 50,
            "title": "Số message tối đa thu từ stream",
        },
        "stream_timeout_s": {
            "type": "integer",
            "minimum": 1,
            "maximum": 120,
            "default": 10,
            "title": "Thời gian chờ stream (giây)",
            "description": "Stream không tự kết thúc (watch…) sẽ dừng sau thời gian này",
        },
        "proto_source": {
            "type": "string",
            "maxLength": MAX_PROTO_CHARS,
            "contentMediaType": "text/x-protobuf",
            "title": "Nội dung file .proto (khi máy chủ không bật reflection)",
            "description": "Một file; chỉ import được google/protobuf/*. Để trống = dùng reflection",
        },
    },
    "required": ["target"],
    "additionalProperties": False,
}

_MANIFEST = PluginManifest(
    id=PLUGIN_ID,
    kind=PluginKind.CONNECTOR,
    name="gRPC",
    description=(
        "Gọi dịch vụ gRPC nội bộ: tự khám phá method qua server reflection, mỗi method "
        "thành một tool (method ghi cần duyệt)."
    ),
    version="1.0.0",
    api_version=PLUGIN_API_VERSION,
    config_schema=_CONFIG_SCHEMA,
    secrets=("credential", CLIENT_KEY_SECRET),
    optional_secrets=("credential", CLIENT_KEY_SECRET),
    dynamic_tools=True,
    ui_hints={
        "icon": "deployed_code",
        "category": "api",
        "multiline_secrets": [CLIENT_KEY_SECRET],
    },
)

_INT32 = {
    FieldDescriptor.TYPE_INT32,
    FieldDescriptor.TYPE_SINT32,
    FieldDescriptor.TYPE_SFIXED32,
    FieldDescriptor.TYPE_UINT32,
    FieldDescriptor.TYPE_FIXED32,
}
_INT64 = {
    FieldDescriptor.TYPE_INT64,
    FieldDescriptor.TYPE_SINT64,
    FieldDescriptor.TYPE_SFIXED64,
    FieldDescriptor.TYPE_UINT64,
    FieldDescriptor.TYPE_FIXED64,
}
_WELL_KNOWN: dict[str, dict[str, Any]] = {
    "google.protobuf.Timestamp": {"type": "string", "format": "date-time"},
    "google.protobuf.Duration": {"type": "string", "description": "Ví dụ '1.5s'"},
    "google.protobuf.Struct": {"type": "object"},
    "google.protobuf.Value": {},
    "google.protobuf.ListValue": {"type": "array"},
    "google.protobuf.Empty": {"type": "object", "additionalProperties": False},
    "google.protobuf.FieldMask": {"type": "string"},
    "google.protobuf.Any": {"type": "object"},
    "google.protobuf.StringValue": {"type": ["string", "null"]},
    "google.protobuf.BytesValue": {"type": ["string", "null"]},
    "google.protobuf.BoolValue": {"type": ["boolean", "null"]},
    "google.protobuf.DoubleValue": {"type": ["number", "null"]},
    "google.protobuf.FloatValue": {"type": ["number", "null"]},
    "google.protobuf.Int32Value": {"type": ["integer", "null"]},
    "google.protobuf.UInt32Value": {"type": ["integer", "null"]},
    "google.protobuf.Int64Value": {"type": ["integer", "string", "null"]},
    "google.protobuf.UInt64Value": {"type": ["integer", "string", "null"]},
}


def slug(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "rpc"
    if text[0].isdigit():
        text = f"rpc_{text}"
    return text[:MAX_SLUG].rstrip("_")


def _scalar(field: FieldDescriptor, depth: int) -> dict[str, Any]:
    t = field.type
    if t in (FieldDescriptor.TYPE_DOUBLE, FieldDescriptor.TYPE_FLOAT):
        return {"type": "number"}
    if t in _INT32:
        return {"type": "integer"}
    if t in _INT64:
        return {"type": ["integer", "string"]}  # JSON proto3: int64 có thể là chuỗi
    if t == FieldDescriptor.TYPE_BOOL:
        return {"type": "boolean"}
    if t == FieldDescriptor.TYPE_STRING:
        return {"type": "string"}
    if t == FieldDescriptor.TYPE_BYTES:
        return {"type": "string", "description": "base64"}
    if t == FieldDescriptor.TYPE_ENUM:
        return {"type": "string", "enum": [v.name for v in field.enum_type.values][:200]}
    if t in (FieldDescriptor.TYPE_MESSAGE, FieldDescriptor.TYPE_GROUP):
        return message_schema(field.message_type, depth + 1)
    return {}


def message_schema(desc: Descriptor, depth: int = 0) -> dict[str, Any]:
    """Descriptor protobuf → JSON Schema (tên field gốc của proto)."""
    if desc.full_name in _WELL_KNOWN:
        return dict(_WELL_KNOWN[desc.full_name])
    if depth > MAX_DEPTH:
        return {"type": "object"}
    props: dict[str, Any] = {}
    for field in desc.fields[:200]:
        if field.type == FieldDescriptor.TYPE_MESSAGE and field.message_type.GetOptions().map_entry:
            value_field = field.message_type.fields_by_name["value"]
            schema: dict[str, Any] = {
                "type": "object",
                "additionalProperties": _scalar(value_field, depth),
            }
        elif field.is_repeated:
            schema = {"type": "array", "items": _scalar(field, depth)}
        else:
            schema = _scalar(field, depth)
        props[field.name] = schema
    return {"type": "object", "properties": props, "additionalProperties": False}


def _stream_kind(m: MethodDescriptor) -> str:
    if m.client_streaming and m.server_streaming:
        return "bidi stream"
    if m.client_streaming:
        return "client stream"
    return "server stream" if m.server_streaming else "unary"


def method_risk(name: str) -> RiskLevel:
    return RiskLevel.READ if slug(name).startswith(_READ_PREFIXES) else RiskLevel.WRITE


def service_allowed(name: str, allowed: list[str]) -> bool:
    if name.startswith(_HIDDEN_SERVICES):
        return False
    if not allowed:
        return True
    return any(name.startswith(r[:-1]) if r.endswith("*") else name == r for r in allowed)


@dataclass
class _Catalog:
    pool: descriptor_pool.DescriptorPool
    services: list[str]
    loaded_at: float


class GrpcConnector(Plugin):
    def __init__(self) -> None:
        self._cache: dict[str, _Catalog] = {}

    @property
    def manifest(self) -> PluginManifest:
        return _MANIFEST

    def network_targets(self, config: dict[str, Any]) -> list[Endpoint]:
        target = config.get("target")
        if not target:
            return []
        default = 443 if config.get("tls", True) else 80
        return [parse_host_port(str(target), default)]

    # ------------------------------------------------------------------ kênh + reflection
    @staticmethod
    def _channel(conn: ConnectionContext) -> grpc.Channel:
        cfg = conn.config
        options = [
            ("grpc.enable_http_proxy", 0),
            ("grpc.max_receive_message_length", MAX_MESSAGE_BYTES),
            ("grpc.max_send_message_length", MAX_MESSAGE_BYTES),
            ("grpc.primary_user_agent", "aries-agent/1.0"),
        ]
        target = str(cfg["target"])
        if cfg.get("tls", True):
            ca, cert, key = material(cfg, conn.secrets)
            creds = grpc.ssl_channel_credentials(
                root_certificates=ca.encode() or None,
                private_key=key.encode() or None,
                certificate_chain=cert.encode() or None,
            )
            return grpc.secure_channel(target, creds, options=options)
        return grpc.insecure_channel(target, options=options)

    @staticmethod
    def _metadata(conn: ConnectionContext) -> list[tuple[str, str]]:
        kind = str(conn.config.get("auth_type", "none"))
        secret = conn.secrets.get("credential", "")
        if kind == "none":
            return []
        if not secret:
            raise ConnectionError("Kết nối thiếu 'credential' cho kiểu xác thực đã chọn.")
        if kind == "bearer":
            return [("authorization", f"Bearer {secret}")]
        return [(str(conn.config.get("auth_name") or "x-api-key"), secret)]

    @staticmethod
    def compile_proto(source: str) -> descriptor_pb2.FileDescriptorSet:
        """Biên dịch .proto bằng protoc trong tiến trình con (có timeout, bắt thông báo lỗi)."""
        with tempfile.TemporaryDirectory(prefix="aries-proto-") as folder:
            src = Path(folder) / "service.proto"
            out = Path(folder) / "out.pb"
            src.write_text(source, encoding="utf-8")
            try:
                done = subprocess.run(  # noqa: S603 - lệnh cố định, nội dung chỉ là file đầu vào
                    [
                        sys.executable,
                        "-m",
                        "grpc_tools.protoc",
                        f"-I{folder}",
                        "--include_imports",
                        f"--descriptor_set_out={out}",
                        "service.proto",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=PROTOC_TIMEOUT_S,
                    cwd=folder,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ConnectionError("Biên dịch .proto quá thời gian.") from exc
            if done.returncode != 0 or not out.exists():
                detail = (done.stderr or done.stdout or "").replace(folder + "/", "").strip()
                raise ConnectionError(f"File .proto không hợp lệ: {detail[:400]}")
            return descriptor_pb2.FileDescriptorSet.FromString(out.read_bytes())

    def _catalog_from_proto(self, source: str) -> _Catalog:
        fds = self.compile_proto(source)
        pool = descriptor_pool.DescriptorPool()
        services: list[str] = []
        for fd in fds.file:  # --include_imports: phụ thuộc đứng trước
            pool.Add(fd)
            if fd.name.startswith("google/protobuf/"):
                continue
            prefix = f"{fd.package}." if fd.package else ""
            services += [prefix + svc.name for svc in fd.service]
        return _Catalog(
            pool,
            sorted(s for s in services if not s.startswith(_HIDDEN_SERVICES)),
            time.monotonic(),
        )

    def _load_catalog(self, conn: ConnectionContext) -> _Catalog:
        """Đồng bộ (chạy trong thread): descriptor từ .proto đã cấu hình, hoặc qua reflection."""
        timeout = float(conn.config.get("timeout_s", 15))
        source = str(conn.config.get("proto_source") or "").strip()
        if source:
            catalog = self._catalog_from_proto(source)
            channel = self._channel(conn)
            try:  # vẫn kiểm máy chủ có kết nối được không
                grpc.channel_ready_future(channel).result(timeout=timeout)
            except grpc.FutureTimeoutError as exc:
                raise ConnectionError("Không kết nối được tới máy chủ gRPC (timeout).") from exc
            finally:
                channel.close()
            return catalog
        channel = self._channel(conn)
        try:
            grpc.channel_ready_future(channel).result(timeout=timeout)
            db = ProtoReflectionDescriptorDatabase(channel)
            services = [s for s in db.get_services() if not s.startswith(_HIDDEN_SERVICES)]
            pool = descriptor_pool.DescriptorPool(db)
            for name in services:
                pool.FindServiceByName(name)  # kéo descriptor về trong lúc kênh còn mở
        except grpc.FutureTimeoutError as exc:
            raise ConnectionError("Không kết nối được tới máy chủ gRPC (timeout).") from exc
        except grpc.RpcError as exc:
            code = exc.code() if hasattr(exc, "code") else None
            if code == grpc.StatusCode.UNIMPLEMENTED:
                raise ConnectionError(
                    "Máy chủ chưa bật gRPC server reflection — dán nội dung file .proto vào cấu hình."
                ) from exc
            raise ConnectionError(f"Lỗi gRPC khi khám phá: {code}") from exc
        finally:
            channel.close()
        return _Catalog(pool, sorted(services), time.monotonic())

    async def _catalog(self, conn: ConnectionContext, *, fresh: bool = False) -> _Catalog:
        digest = hashlib.sha256(json.dumps(conn.config, sort_keys=True).encode()).hexdigest()
        key = f"{conn.id}:{digest}"
        cached = self._cache.get(key)
        if cached and not fresh and time.monotonic() - cached.loaded_at < CACHE_TTL_S:
            return cached
        catalog = await asyncio.to_thread(self._load_catalog, conn)
        if len(self._cache) >= 100:
            self._cache.clear()
        self._cache[key] = catalog
        return catalog

    def _methods(self, conn: ConnectionContext, catalog: _Catalog) -> list[MethodDescriptor]:
        allowed = list(conn.config.get("allowed_services") or [])
        out = []
        for name in catalog.services:
            if not service_allowed(name, allowed):
                continue
            service = catalog.pool.FindServiceByName(name)
            out.extend(service.methods)
        return out

    # ------------------------------------------------------------------ plugin API
    async def discover_tools(self, connection: ConnectionContext) -> list[ToolSpec]:
        catalog = await self._catalog(connection, fresh=True)
        limit = int(connection.config.get("max_methods", 60))
        tools: list[ToolSpec] = []
        used: set[str] = set()
        for m in self._methods(connection, catalog):
            base = slug(f"{m.containing_service.name}_{m.name}")
            name, n = base, 2
            while name in used:
                name = f"{base[: MAX_SLUG - 3]}_{n}"
                n += 1
            used.add(name)
            full = m.containing_service.full_name
            schema = message_schema(m.input_type)
            if m.client_streaming:  # client-stream / bidi: gửi một loạt message
                schema = {
                    "type": "object",
                    "properties": {
                        "requests": {
                            "type": "array",
                            "items": schema,
                            "minItems": 1,
                            "maxItems": MAX_STREAM_REQUESTS,
                        }
                    },
                    "required": ["requests"],
                    "additionalProperties": False,
                }
            tools.append(
                ToolSpec(
                    name=f"{PLUGIN_ID}.{name}",
                    description=(
                        f"gRPC {full}/{m.name} ({_stream_kind(m)}, input {m.input_type.full_name})"
                    )[:1000],
                    risk=method_risk(m.name),
                    input_schema=schema,
                    binding={
                        "service": full,
                        "method": m.name,
                        "client_streaming": bool(m.client_streaming),
                        "server_streaming": bool(m.server_streaming),
                    },
                )
            )
            if len(tools) >= limit:
                break
        return tools

    def _invoke(self, conn: ConnectionContext, m: MethodDescriptor, request: dict[str, Any]) -> str:
        """Đồng bộ (chạy trong thread): JSON → protobuf → gọi (unary/streaming) → JSON."""
        req_cls = message_factory.GetMessageClass(m.input_type)
        resp_cls = message_factory.GetMessageClass(m.output_type)
        if m.client_streaming:
            raw = request.get("requests")
            if not isinstance(raw, list) or not raw:
                raise json_format.ParseError("Cần mảng 'requests' (ít nhất một message).")
            messages = [json_format.ParseDict(r, req_cls()) for r in raw[:MAX_STREAM_REQUESTS]]
        else:
            messages = [json_format.ParseDict(request, req_cls())]
        cfg = conn.config
        path = f"/{m.containing_service.full_name}/{m.name}"
        ser = {
            "request_serializer": lambda msg: msg.SerializeToString(),
            "response_deserializer": resp_cls.FromString,
        }
        metadata = self._metadata(conn)

        def as_dict(msg: Any) -> dict[str, Any]:
            return dict(json_format.MessageToDict(msg, preserving_proto_field_name=True))

        channel = self._channel(conn)
        try:
            if not m.server_streaming:
                timeout = float(cfg.get("timeout_s", 15))
                if m.client_streaming:
                    single = channel.stream_unary(path, **ser)
                    response = single(iter(messages), timeout=timeout, metadata=metadata)
                else:
                    unary = channel.unary_unary(path, **ser)
                    response = unary(messages[0], timeout=timeout, metadata=metadata)
                return json.dumps(as_dict(response), ensure_ascii=False)

            limit = int(cfg.get("max_stream_messages", 50))
            timeout = float(cfg.get("stream_timeout_s", 10))
            if m.client_streaming:
                stream = channel.stream_stream(path, **ser)(
                    iter(messages), timeout=timeout, metadata=metadata
                )
            else:
                stream = channel.unary_stream(path, **ser)(
                    messages[0], timeout=timeout, metadata=metadata
                )
            out: list[dict[str, Any]] = []
            ended = "completed"
            try:
                for msg in stream:
                    out.append(as_dict(msg))
                    if len(out) >= limit:
                        ended = "limit"
                        stream.cancel()
                        break
            except grpc.RpcError as exc:
                # Stream không tự kết thúc (watch…): hết thời gian chờ → trả phần đã nhận.
                if exc.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
                    raise
                ended = "timeout"
            return json.dumps(
                {"messages": out, "count": len(out), "ended": ended}, ensure_ascii=False
            )
        finally:
            channel.close()

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        conn, tool = ctx.connection, ctx.tool
        if conn is None or tool is None:
            return ToolResult("Tool này cần một kết nối gRPC.", is_error=True)
        service, method = tool.binding.get("service"), tool.binding.get("method")
        if not service or not method:
            return ToolResult(f"Tool '{name}' không còn hợp lệ — hãy đồng bộ lại.", is_error=True)
        if not service_allowed(str(service), list(conn.config.get("allowed_services") or [])):
            return ToolResult(
                f"Service {service} không nằm trong phạm vi được phép.", is_error=True
            )
        try:
            catalog = await self._catalog(conn)
            desc = catalog.pool.FindServiceByName(str(service)).FindMethodByName(str(method))
            if desc is None:
                return ToolResult(f"Máy chủ không còn method {service}/{method}.", is_error=True)
            text = await asyncio.to_thread(self._invoke, conn, desc, dict(arguments))
        except json_format.ParseError as exc:
            return ToolResult(f"Input không khớp protobuf: {str(exc)[:300]}", is_error=True)
        except (ConnectionError, TlsConfigError) as exc:
            return ToolResult(str(exc), is_error=True)
        except KeyError:
            return ToolResult(f"Máy chủ không còn service {service}.", is_error=True)
        except grpc.RpcError as exc:
            code = exc.code() if hasattr(exc, "code") else "?"
            detail = exc.details() if hasattr(exc, "details") else ""
            return ToolResult(f"gRPC {code}: {str(detail)[:300]}", is_error=True)
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n…[đã cắt bớt]"
        return ToolResult(text)

    async def test_connection(self, connection: ConnectionContext) -> HealthStatus:
        try:
            self._metadata(connection)
            catalog = await self._catalog(connection, fresh=True)
        except (ConnectionError, TlsConfigError) as exc:
            return HealthStatus(False, str(exc))
        methods = self._methods(connection, catalog)
        return HealthStatus(
            True,
            f"Kết nối OK — {len(catalog.services)} service, {len(methods)} method trong phạm vi.",
        )
