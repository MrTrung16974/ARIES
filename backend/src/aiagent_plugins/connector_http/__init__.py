"""Connector REST / HTTP API (ADR-0023).

- Một connection = một API (`base_url`) + cách xác thực (không, Bearer, API key ở header/query,
  Basic). Secret `credential` (token/API key/mật khẩu) do Credential Broker cấp mỗi lần gọi.
- Tool động theo connection:
  - `http_api.get`: GET đường dẫn bất kỳ dưới `base_url` (chỉ đọc);
  - `http_api.send`: POST/PUT/PATCH/DELETE (chỉ khi `allow_write`; mức destructive → luôn duyệt);
  - mỗi thao tác trong tài liệu OpenAPI (nếu có `openapi_url`) thành một tool riêng.
- An toàn: không thoát khỏi `base_url` (chặn `..`, URL tuyệt đối), giới hạn `allowed_paths`, không tự
  theo redirect, giới hạn kích thước phản hồi, xóa credential nếu API vọng lại. Kết quả là dữ liệu
  không tin cậy (core gắn taint). Đích công khai đi qua egress proxy; đích nội bộ đi thẳng.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpx
import yaml

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
    ToolResult,
    ToolSpec,
)
from aiagent.plugin_sdk.net import is_public
from aiagent.plugin_sdk.tls import CLIENT_KEY_SECRET, TlsConfigError, ssl_context, tls_properties
from aiagent_plugins.connector_http.openapi import FILE_ARG, OpenApiError, operations, title_of

logger = logging.getLogger(__name__)

PLUGIN_ID = "http_api"
MAX_RESPONSE_BYTES = 1_000_000
MAX_SPEC_BYTES = 5_000_000
MAX_TEXT_CHARS = 12_000
USER_AGENT = "ARIES-agent/1.0 (+on-prem)"
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_TEXT_TYPES = ("json", "text/", "xml", "yaml", "csv", "javascript")

AUTH_TYPES = [
    "none",
    "bearer",
    "api_key_header",
    "api_key_query",
    "basic",
    "oauth2_client_credentials",
]
BODY_TYPES = ["json", "form", "multipart", "text"]
MAX_UPLOAD_BYTES = 5_000_000
TOKEN_MARGIN_S = 60

_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "base_url": {
            "type": "string",
            "minLength": 8,
            "maxLength": 500,
            "pattern": r"^https?://[^\s/?#]+[^\s?#]*$",
            "title": "URL gốc của API",
            "description": "Ví dụ https://erp.congty.vn/api/v1 (không kèm ?query)",
        },
        "auth_type": {
            "type": "string",
            "enum": AUTH_TYPES,
            "default": "none",
            "title": "Kiểu xác thực",
            "description": "Token/API key/mật khẩu nhập ở ô 'credential'",
        },
        "auth_name": {
            "type": "string",
            "pattern": r"^[A-Za-z0-9_\-]{1,100}$",
            "default": "X-API-Key",
            "title": "Tên header/tham số chứa API key",
        },
        "username": {"type": "string", "maxLength": 200, "title": "Tên đăng nhập (Basic)"},
        "token_url": {
            "type": "string",
            "maxLength": 500,
            "pattern": r"^https?://[^\s]+$",
            "title": "Token URL (OAuth2 client credentials)",
            "description": "Client secret nhập ở ô 'credential'",
        },
        "client_id": {"type": "string", "maxLength": 300, "title": "Client ID (OAuth2)"},
        "oauth_scope": {"type": "string", "maxLength": 1000, "title": "Scope (OAuth2, tùy chọn)"},
        "token_auth": {
            "type": "string",
            "enum": ["basic", "body"],
            "default": "basic",
            "title": "Gửi client secret qua (OAuth2)",
        },
        "openapi_url": {
            "type": "string",
            "maxLength": 500,
            "title": "Tài liệu OpenAPI (URL hoặc đường dẫn)",
            "description": "Ví dụ /openapi.json — để trống: chỉ có tool gọi chung",
        },
        "allowed_paths": {
            "type": "array",
            "items": {"type": "string", "pattern": "^/", "maxLength": 200},
            "maxItems": 50,
            "default": [],
            "title": "Đường dẫn được phép (tiền tố, trống = tất cả)",
        },
        "allow_write": {
            "type": "boolean",
            "default": False,
            "title": "Cho phép ghi (POST/PUT/PATCH/DELETE — cần duyệt)",
        },
        "verify_tls": {"type": "boolean", "default": True, "title": "Kiểm tra chứng chỉ TLS"},
        **tls_properties(),
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 120, "default": 20},
        "max_operations": {
            "type": "integer",
            "minimum": 1,
            "maximum": 150,
            "default": 60,
            "title": "Số thao tác tối đa lấy từ OpenAPI",
        },
    },
    "required": ["base_url"],
    "additionalProperties": False,
}

_MANIFEST = PluginManifest(
    id=PLUGIN_ID,
    kind=PluginKind.CONNECTOR,
    name="REST / HTTP API",
    description=(
        "Gọi API REST nội bộ hoặc bên ngoài: GET chỉ đọc, ghi khi được bật (luôn cần duyệt); "
        "nhập tài liệu OpenAPI để mỗi thao tác thành một tool."
    ),
    version="1.0.0",
    api_version=PLUGIN_API_VERSION,
    config_schema=_CONFIG_SCHEMA,
    secrets=("credential", CLIENT_KEY_SECRET),
    optional_secrets=("credential", CLIENT_KEY_SECRET),  # API không xác thực thì để trống
    dynamic_tools=True,
    ui_hints={"icon": "cable", "category": "api", "multiline_secrets": [CLIENT_KEY_SECRET]},
)

_PATH_ARG = {
    "type": "string",
    "minLength": 1,
    "maxLength": 1000,
    "description": "Đường dẫn dưới URL gốc, bắt đầu bằng /, ví dụ /orders/123 (không kèm ?query)",
}
_QUERY_ARG = {
    "type": "object",
    "description": 'Tham số query, ví dụ {"page": 1}',
    "additionalProperties": {"type": ["string", "number", "integer", "boolean"]},
}


def _generic_tools(allow_write: bool) -> list[ToolSpec]:
    tools = [
        ToolSpec(
            name=f"{PLUGIN_ID}.get",
            description="Gọi GET một đường dẫn của API (chỉ đọc). Trả về mã HTTP và nội dung.",
            risk=RiskLevel.READ,
            input_schema={
                "type": "object",
                "properties": {"path": _PATH_ARG, "query": _QUERY_ARG},
                "required": ["path"],
                "additionalProperties": False,
            },
            binding={"generic": "get"},
        )
    ]
    if allow_write:
        tools.append(
            ToolSpec(
                name=f"{PLUGIN_ID}.send",
                description=(
                    "Gửi yêu cầu ghi (POST/PUT/PATCH/DELETE) tới API. Body JSON (mặc định), form, "
                    "multipart (kèm files) hoặc text. Chỉ dùng khi người dùng yêu cầu rõ ràng; "
                    "luôn cần duyệt."
                ),
                risk=RiskLevel.DESTRUCTIVE,
                input_schema={
                    "type": "object",
                    "properties": {
                        "method": {"type": "string", "enum": list(WRITE_METHODS)},
                        "path": _PATH_ARG,
                        "query": _QUERY_ARG,
                        "body_type": {"type": "string", "enum": BODY_TYPES, "default": "json"},
                        "body": {
                            "description": (
                                "json: object/array; form/multipart: object giá trị đơn; text: chuỗi"
                            )
                        },
                        "files": {
                            "type": "array",
                            "maxItems": 10,
                            "description": "Chỉ với multipart: các tệp tải lên",
                            "items": {
                                **FILE_ARG,
                                "properties": {
                                    **FILE_ARG["properties"],
                                    "field": {"type": "string", "maxLength": 100},
                                },
                            },
                        },
                    },
                    "required": ["method", "path"],
                    "additionalProperties": False,
                },
                binding={"generic": "send"},
            )
        )
    return tools


class RequestRejectedError(ValueError):
    """Yêu cầu bị chặn trước khi gửi — thông điệp an toàn cho model."""


def _host_port(url: str) -> Endpoint:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return Endpoint(parts.hostname or "", port)


def _allowed(path: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    return any(path == a.rstrip("/") or path.startswith(a.rstrip("/") + "/") for a in allowed)


def build_url(base_url: str, path: str, allowed_paths: list[str]) -> str:
    """Ghép `path` dưới `base_url`; không cho thoát ra ngoài hay vượt `allowed_paths`."""
    path = path.strip()
    if not path.startswith("/"):
        path = "/" + path
    if "://" in path or path.startswith("//") or "\\" in path or "?" in path or "#" in path:
        raise RequestRejectedError("Đường dẫn không hợp lệ (dùng tham số 'query' cho ?…).")
    if any(seg in (".", "..") for seg in unquote(path).split("/")):
        raise RequestRejectedError("Đường dẫn không được chứa '.' hoặc '..'.")
    if not _allowed(path, allowed_paths):
        raise RequestRejectedError(f"Đường dẫn {path} không nằm trong phạm vi được phép.")
    url = base_url.rstrip("/") + path
    if urlsplit(url).netloc != urlsplit(base_url).netloc:
        raise RequestRejectedError("Đường dẫn không hợp lệ.")
    return url


def fill_path(template: str, args: dict[str, Any], where: dict[str, str]) -> str:
    def value(name: str) -> str:
        raw = args.get(name)
        if raw is None or str(raw) == "":
            raise RequestRejectedError(f"Thiếu tham số đường dẫn '{name}'.")
        return quote(str(raw), safe="")

    out = template
    for name, loc in where.items():
        if loc == "path":
            out = out.replace("{" + name + "}", value(name))
    if "{" in out or "}" in out:
        raise RequestRejectedError("Thiếu tham số đường dẫn.")
    return out


def _query(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if v is None:
            continue
        out[str(k)] = ("true" if v else "false") if isinstance(v, bool) else str(v)
    return out


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _is_file(value: Any) -> bool:
    return (
        isinstance(value, dict) and "filename" in value and ("text" in value or "base64" in value)
    )


def _file_bytes(spec: dict[str, Any]) -> tuple[str, bytes, str]:
    filename = str(spec.get("filename") or "file")[:200]
    if spec.get("base64") is not None:
        try:
            data = base64.b64decode(str(spec["base64"]), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RequestRejectedError(f"Tệp '{filename}': base64 không hợp lệ.") from exc
    else:
        data = str(spec.get("text", "")).encode("utf-8")
    ctype = str(spec.get("content_type") or "application/octet-stream")[:100]
    return filename, data, ctype


def encode_body(kind: str, body: Any, files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Tham số build_request của httpx cho từng kiểu body. Giới hạn tổng dung lượng tệp."""
    if kind == "json":
        return {"json": body} if body is not None else {}
    if kind == "text":
        if body is None:
            return {}
        return {
            "content": str(body).encode(),
            "headers": {"Content-Type": "text/plain; charset=utf-8"},
        }
    if body is not None and not isinstance(body, dict):
        raise RequestRejectedError("Body form/multipart phải là object.")
    fields: dict[str, Any] = {}
    uploads: list[tuple[str, tuple[str, bytes, str]]] = []
    for key, value in (body or {}).items():
        items = value if isinstance(value, list) else [value]
        if kind == "multipart" and items and all(_is_file(v) for v in items):
            uploads += [(str(key), _file_bytes(v)) for v in items]
        elif value is not None:
            fields[str(key)] = (
                [_scalar(v) for v in value] if isinstance(value, list) else _scalar(value)
            )
    if kind == "form":
        return {"data": fields} if fields else {}
    if kind != "multipart":
        raise RequestRejectedError(f"Kiểu body không hỗ trợ: {kind}")
    for spec in files or []:
        uploads.append((str(spec.get("field") or "file"), _file_bytes(spec)))
    if sum(len(f[1][1]) for f in uploads) > MAX_UPLOAD_BYTES:
        raise RequestRejectedError(f"Tổng dung lượng tệp vượt {MAX_UPLOAD_BYTES // 1_000_000} MB.")
    if not uploads:  # multipart không tệp vẫn phải là multipart
        return {"files": [(k, (None, v)) for k, v in fields.items() if not isinstance(v, list)]}
    return {"data": fields, "files": uploads}


class HttpApiConnector(Plugin):
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport  # test truyền MockTransport
        # Access token OAuth2 client credentials: khóa (connection + cấu hình + secret) → (token, hết hạn)
        self._tokens: dict[str, tuple[str, float]] = {}

    @property
    def manifest(self) -> PluginManifest:
        return _MANIFEST

    # ------------------------------------------------------------------ mạng
    def network_targets(self, config: dict[str, Any]) -> list[Endpoint]:
        targets = []
        base = str(config.get("base_url") or "")
        if base:
            targets.append(_host_port(base))
        spec = str(config.get("openapi_url") or "")
        if spec.startswith(("http://", "https://")):
            targets.append(_host_port(spec))
        token_url = str(config.get("token_url") or "")
        if config.get("auth_type") == "oauth2_client_credentials" and token_url:
            targets.append(_host_port(token_url))
        return targets

    async def _client(self, conn: ConnectionContext, url: str) -> httpx.AsyncClient:
        cfg = conn.config
        kwargs: dict[str, Any] = {
            "timeout": float(cfg.get("timeout_s", 20)),
            "verify": ssl_context(cfg, conn.secrets, verify=bool(cfg.get("verify_tls", True))),
            "follow_redirects": False,
            "headers": {"User-Agent": USER_AGENT},
        }
        if self._transport is not None:
            return httpx.AsyncClient(transport=self._transport, **kwargs)
        # Đích công khai → qua egress proxy (HTTPS_PROXY); đích nội bộ → đi thẳng.
        via_proxy = False
        if conn.network is not None:
            ips = await conn.network.check(_host_port(url))
            via_proxy = bool(ips) and all(is_public(ip) for ip in ips)
        return httpx.AsyncClient(trust_env=via_proxy, **kwargs)

    # ------------------------------------------------------------------ xác thực
    @staticmethod
    def _check_auth(conn: ConnectionContext) -> str:
        """Kiểu xác thực (đã kiểm có credential nếu cần)."""
        kind = str(conn.config.get("auth_type", "none"))
        if kind not in AUTH_TYPES:
            raise RequestRejectedError(f"Kiểu xác thực không hỗ trợ: {kind}")
        if kind != "none" and not conn.secrets.get("credential"):
            raise RequestRejectedError("Kết nối thiếu 'credential' cho kiểu xác thực đã chọn.")
        if kind == "oauth2_client_credentials" and not (
            conn.config.get("token_url") and conn.config.get("client_id")
        ):
            raise RequestRejectedError("OAuth2 client credentials cần token_url và client_id.")
        return kind

    async def _auth(
        self, conn: ConnectionContext, *, fresh: bool = False
    ) -> tuple[dict[str, str], dict[str, str], httpx.Auth | None]:
        cfg, secret = conn.config, conn.secrets.get("credential", "")
        kind = self._check_auth(conn)
        name = str(cfg.get("auth_name") or "X-API-Key")
        if kind == "none":
            return {}, {}, None
        if kind == "bearer":
            return {"Authorization": f"Bearer {secret}"}, {}, None
        if kind == "api_key_header":
            return {name: secret}, {}, None
        if kind == "api_key_query":
            return {}, {name: secret}, None
        if kind == "basic":
            return {}, {}, httpx.BasicAuth(str(cfg.get("username", "")), secret)
        token = await self._client_credentials_token(conn, fresh=fresh)
        return {"Authorization": f"Bearer {token}"}, {}, None

    def _token_key(self, conn: ConnectionContext) -> str:
        cfg = conn.config
        raw = "|".join(
            str(x)
            for x in (
                conn.id,
                cfg.get("token_url"),
                cfg.get("client_id"),
                cfg.get("oauth_scope"),
                cfg.get("token_auth"),
                conn.secrets.get("credential"),
            )
        )
        return hashlib.sha256(raw.encode()).hexdigest()

    async def _client_credentials_token(self, conn: ConnectionContext, *, fresh: bool) -> str:
        """OAuth2 client credentials (RFC 6749 §4.4); cache tới gần lúc hết hạn."""
        key = self._token_key(conn)
        cached = self._tokens.get(key)
        if cached and not fresh and cached[1] - time.time() > TOKEN_MARGIN_S:
            return cached[0]
        cfg, secret = conn.config, conn.secrets.get("credential", "")
        url = str(cfg["token_url"])
        form = {"grant_type": "client_credentials"}
        if cfg.get("oauth_scope"):
            form["scope"] = str(cfg["oauth_scope"])
        auth: httpx.Auth | None = None
        if cfg.get("token_auth", "basic") == "body":
            form |= {"client_id": str(cfg["client_id"]), "client_secret": secret}
        else:
            auth = httpx.BasicAuth(str(cfg["client_id"]), secret)
        async with await self._client(conn, url) as http:
            try:
                r = await http.post(
                    url,
                    data=form,
                    headers={"Accept": "application/json"},
                    auth=auth if auth is not None else httpx.USE_CLIENT_DEFAULT,
                )
            except httpx.HTTPError as exc:
                raise RequestRejectedError(
                    f"Không gọi được token URL ({type(exc).__name__})."
                ) from exc
        if r.status_code != 200:
            raise RequestRejectedError(
                f"Máy chủ token từ chối cấp access token (HTTP {r.status_code})."
            )
        try:
            data = r.json()
            token = str(data["access_token"])
        except (ValueError, KeyError, TypeError) as exc:
            raise RequestRejectedError("Phản hồi của token URL không có access_token.") from exc
        try:
            expires_in = float(data.get("expires_in") or 300)
        except (TypeError, ValueError):
            expires_in = 300.0
        if len(self._tokens) >= 500:
            self._tokens.clear()
        self._tokens[key] = (token, time.time() + expires_in)
        return token

    def _redact(self, text: str, conn: ConnectionContext) -> str:
        secrets = [conn.secrets.get("credential", "")]
        cached = self._tokens.get(self._token_key(conn))
        if cached:
            secrets.append(cached[0])
        for secret in secrets:
            if len(secret) >= 4:
                text = text.replace(secret, "***")
        return text

    # ------------------------------------------------------------------ gọi API
    async def _send(
        self,
        conn: ConnectionContext,
        method: str,
        url: str,
        query: dict[str, str],
        body: Any = None,
        *,
        body_kind: str = "json",
        files: list[dict[str, Any]] | None = None,
        with_auth: bool = True,
        limit: int = MAX_RESPONSE_BYTES,
    ) -> tuple[httpx.Response, bytes]:
        response, data = await self._send_once(
            conn, method, url, query, body, body_kind, files, with_auth, limit, fresh=False
        )
        if (
            response.status_code == 401
            and with_auth
            and conn.config.get("auth_type") == "oauth2_client_credentials"
        ):  # token bị thu hồi sớm → lấy token mới, thử lại đúng một lần
            response, data = await self._send_once(
                conn, method, url, query, body, body_kind, files, with_auth, limit, fresh=True
            )
        return response, data

    async def _send_once(
        self,
        conn: ConnectionContext,
        method: str,
        url: str,
        query: dict[str, str],
        body: Any,
        body_kind: str,
        files: list[dict[str, Any]] | None,
        with_auth: bool,
        limit: int,
        *,
        fresh: bool,
    ) -> tuple[httpx.Response, bytes]:
        headers, auth_query, auth = (
            await self._auth(conn, fresh=fresh) if with_auth else ({}, {}, None)
        )
        encoded = encode_body(body_kind, body, files)
        headers = {"Accept": "application/json, */*;q=0.5", **headers, **encoded.pop("headers", {})}
        async with await self._client(conn, url) as http:
            request = http.build_request(
                method,
                url,
                params={**query, **auth_query},
                headers=headers,
                **encoded,
            )
            response = await http.send(request, auth=auth, stream=True)
            try:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if len(data) > limit:
                        raise RequestRejectedError(f"Phản hồi lớn hơn {limit // 1000} KB.")
            finally:
                await response.aclose()
        return response, bytes(data)

    def _format(self, conn: ConnectionContext, response: httpx.Response, data: bytes) -> ToolResult:
        status = response.status_code
        ctype = response.headers.get("content-type", "").lower()
        if 300 <= status < 400:
            location = response.headers.get("location", "")
            return ToolResult(
                f"HTTP {status}: API chuyển hướng tới {location[:300]} (không tự theo).",
                is_error=True,
            )
        if not data:
            text = "(không có nội dung)"
        elif "json" in ctype:
            try:
                text = json.dumps(json.loads(data), ensure_ascii=False)
            except ValueError:
                text = data.decode("utf-8", "replace")
        elif any(t in ctype for t in _TEXT_TYPES) or not ctype:
            text = data.decode("utf-8", "replace")
        else:
            text = f"(nội dung {ctype}, {len(data)} byte — không hiển thị)"
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n…[đã cắt bớt]"
        hint = {401: " (sai/thiếu thông tin xác thực)", 403: " (không có quyền)"}.get(status, "")
        content = self._redact(f"HTTP {status}{hint}\n{text}", conn)
        return ToolResult(
            content,
            is_error=status >= 400,
            data={"status": status, "bytes": len(data)},
        )

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        conn, tool = ctx.connection, ctx.tool
        if conn is None or tool is None:
            return ToolResult("Tool này cần một kết nối REST/HTTP API.", is_error=True)
        cfg = conn.config
        allowed = list(cfg.get("allowed_paths") or [])
        binding = tool.binding
        try:
            body_kind, files = "json", []
            if binding.get("generic") == "get":
                method, path = "GET", str(arguments.get("path", ""))
                query, body = _query(arguments.get("query")), None
            elif binding.get("generic") == "send":
                method, path = (
                    str(arguments.get("method", "")).upper(),
                    str(arguments.get("path", "")),
                )
                query, body = _query(arguments.get("query")), arguments.get("body")
                body_kind = str(arguments.get("body_type") or "json")
                files = list(arguments.get("files") or [])
                if files and body_kind != "multipart":
                    raise RequestRejectedError("Chỉ gửi kèm tệp với body_type = multipart.")
                if method not in WRITE_METHODS:
                    raise RequestRejectedError("Method phải là POST/PUT/PATCH/DELETE.")
            elif "path" in binding and "method" in binding:
                where = dict(binding.get("params") or {})
                method = str(binding["method"])
                path = fill_path(str(binding["path"]), arguments, where)
                query = _query({k: arguments.get(k) for k, loc in where.items() if loc == "query"})
                media = binding.get("body")
                body = arguments.get("body") if media else None
                body_kind = "json" if media in (True, None) else str(media)
            else:
                return ToolResult(
                    f"Tool '{name}' không còn hợp lệ — hãy đồng bộ lại.", is_error=True
                )
            if method != "GET" and not cfg.get("allow_write", False):
                raise RequestRejectedError("Kết nối này chỉ đọc (allow_write = false).")
            url = build_url(str(cfg["base_url"]), path, allowed)
            response, data = await self._send(
                conn, method, url, query, body, body_kind=body_kind, files=files
            )
        except (RequestRejectedError, BlockedEndpointError, TlsConfigError) as exc:
            return ToolResult(str(exc), is_error=True)
        except httpx.TimeoutException:
            return ToolResult("API không phản hồi kịp (timeout).", is_error=True)
        except httpx.HTTPError as exc:
            logger.info("Gọi API lỗi: %s", type(exc).__name__)
            return ToolResult(f"Không gọi được API ({type(exc).__name__}).", is_error=True)
        return self._format(conn, response, data)

    # ------------------------------------------------------------------ OpenAPI
    def _spec_url(self, cfg: dict[str, Any]) -> tuple[str, bool]:
        """(URL tài liệu, có gửi credential không). Chỉ gửi credential cho cùng máy với API."""
        base = str(cfg["base_url"])
        raw = str(cfg.get("openapi_url") or "").strip()
        if raw.startswith(("http://", "https://")):
            return raw, urlsplit(raw).netloc == urlsplit(base).netloc
        if not raw.startswith("/"):
            raw = "/" + raw
        return build_url(base, raw, []), True

    async def _load_spec(self, conn: ConnectionContext) -> dict[str, Any]:
        url, same_origin = self._spec_url(conn.config)
        try:
            response, data = await self._send(
                conn, "GET", url, {}, with_auth=same_origin, limit=MAX_SPEC_BYTES
            )
        except RequestRejectedError as exc:
            raise ConnectionError(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ConnectionError(
                f"Không tải được tài liệu OpenAPI ({type(exc).__name__})."
            ) from exc
        if response.status_code != 200:
            raise ConnectionError(f"Tải tài liệu OpenAPI trả về HTTP {response.status_code}.")
        try:
            spec = json.loads(data)
        except ValueError:
            try:
                spec = yaml.safe_load(data.decode("utf-8", "replace"))
            except yaml.YAMLError as exc:
                raise ConnectionError("Tài liệu OpenAPI không phải JSON/YAML hợp lệ.") from exc
        if not isinstance(spec, dict):
            raise ConnectionError("Tài liệu OpenAPI không hợp lệ.")
        return spec

    def _operations(self, cfg: dict[str, Any], spec: dict[str, Any]) -> list[ToolSpec]:
        try:
            return operations(
                spec,
                PLUGIN_ID,
                allow_write=bool(cfg.get("allow_write", False)),
                allowed_paths=list(cfg.get("allowed_paths") or []),
                limit=int(cfg.get("max_operations", 60)),
            )
        except OpenApiError as exc:
            raise ConnectionError(str(exc)) from exc

    async def discover_tools(self, connection: ConnectionContext) -> list[ToolSpec]:
        cfg = connection.config
        tools = _generic_tools(bool(cfg.get("allow_write", False)))
        if cfg.get("openapi_url"):
            tools += self._operations(cfg, await self._load_spec(connection))
        return tools

    async def test_connection(self, connection: ConnectionContext) -> HealthStatus:
        cfg = connection.config
        try:
            await self._auth(connection, fresh=True)  # OAuth2: lấy token thật để kiểm
            if cfg.get("openapi_url"):
                spec = await self._load_spec(connection)
                ops = self._operations(cfg, spec)
                return HealthStatus(True, f"Kết nối OK — {title_of(spec)}, {len(ops)} thao tác.")
            url = build_url(str(cfg["base_url"]), "/", [])
            response, _ = await self._send(connection, "GET", url, {}, limit=MAX_RESPONSE_BYTES)
        except (RequestRejectedError, BlockedEndpointError, ConnectionError, TlsConfigError) as exc:
            return HealthStatus(False, self._redact(str(exc), connection))
        except httpx.TimeoutException:
            return HealthStatus(False, "API không phản hồi kịp (timeout).")
        except httpx.HTTPError as exc:
            return HealthStatus(False, f"Không kết nối được tới API ({type(exc).__name__}).")
        status = response.status_code
        if status in (401, 403):
            return HealthStatus(
                False, f"HTTP {status} — sai thông tin xác thực hoặc không có quyền."
            )
        if status >= 500:
            return HealthStatus(False, f"API lỗi phía máy chủ (HTTP {status}).")
        return HealthStatus(True, f"Kết nối OK — HTTP {status}.")


__all__ = ["HttpApiConnector", "build_url", "fill_path"]
