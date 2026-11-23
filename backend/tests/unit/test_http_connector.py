"""Connector REST / HTTP API (ADR-0023)."""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from aiagent.bootstrap.container import PluginConnectorLookup
from aiagent.bootstrap.plugins import PluginRegistry
from aiagent.modules.audit.application import AuditService
from aiagent.modules.connections.application import ConnectionService
from aiagent.modules.connections.domain import ConnectionScope
from aiagent.modules.identity.domain import Principal, Role
from aiagent.plugin_sdk import ConnectionContext, RiskLevel, ToolContext, ToolSpec
from aiagent_plugins.connector_http import HttpApiConnector, build_url
from aiagent_plugins.connector_http.openapi import operations, slug
from tests.fakes import (
    InMemoryAuditLog,
    InMemoryConnectionRepository,
    InMemoryVault,
    _StaticEntryPoint,
)

BASE = "https://erp.corp.vn/api/v1"
TOKEN = "s3cr3t-token"

SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "ERP", "version": "2.1"},
    "paths": {
        "/orders": {
            "get": {
                "operationId": "listOrders",
                "summary": "Danh sách đơn hàng",
                "parameters": [
                    {"name": "status", "in": "query", "schema": {"type": "string"}},
                    {"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
                ],
            },
            "post": {
                "operationId": "createOrder",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Order"}}
                    },
                },
            },
        },
        "/orders/{id}": {
            "parameters": [{"$ref": "#/components/parameters/OrderId"}],
            "get": {"operationId": "get", "summary": "Trùng tên tool chung"},
            "delete": {"operationId": "deleteOrder"},
        },
        "/admin/users": {"get": {"operationId": "listUsers"}},
        "/legacy": {"get": {"operationId": "legacy", "deprecated": True}},
    },
    "components": {
        "parameters": {
            "OrderId": {"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}
        },
        "schemas": {
            "Order": {
                "type": "object",
                "required": ["sku"],
                "properties": {
                    "sku": {"type": "string", "example": "A1"},
                    "qty": {"type": "integer", "nullable": True},
                    "parent": {"$ref": "#/components/schemas/Order"},  # đệ quy
                },
            }
        },
    },
}


# ---------------------------------------------------------------- build_url
@pytest.mark.parametrize(
    "path",
    ["/../secret", "/a/%2e%2e/b", "//evil.com/x", "http://evil.com", "/x?y=1", "/a\\b", "/admin"],
)
def test_build_url_rejects_escape(path: str) -> None:
    with pytest.raises(ValueError):
        build_url(BASE, path, ["/orders", "/products"])


def test_build_url_ok() -> None:
    assert build_url(BASE, "orders/7", []) == f"{BASE}/orders/7"
    assert build_url(BASE + "/", "/orders", ["/orders"]) == f"{BASE}/orders"


# ---------------------------------------------------------------- OpenAPI
def test_openapi_operations_read_only_by_default() -> None:
    tools = operations(SPEC, "http_api", allow_write=False, allowed_paths=["/orders"], limit=50)
    names = [t.name for t in tools]
    assert names == ["http_api.list_orders", "http_api.get_2"]  # 'get' trùng tool chung
    lst = tools[0]
    assert lst.risk is RiskLevel.READ
    assert lst.binding == {
        "method": "GET",
        "path": "/orders",
        "params": {"status": "query"},
        "body": None,
    }
    assert set(lst.input_schema["properties"]) == {"status"}  # header bị bỏ
    assert tools[1].input_schema["required"] == ["id"]


def test_openapi_operations_with_write_and_schemas_valid() -> None:
    tools = operations(SPEC, "http_api", allow_write=True, allowed_paths=[], limit=50)
    by = {t.name: t for t in tools}
    assert by["http_api.create_order"].risk is RiskLevel.WRITE
    assert by["http_api.delete_order"].risk is RiskLevel.DESTRUCTIVE
    assert "http_api.legacy" not in by and "http_api.list_users" in by
    body = by["http_api.create_order"].input_schema
    assert body["required"] == ["body"]
    assert body["properties"]["body"]["properties"]["qty"]["type"] == ["integer", "null"]
    assert "example" not in json.dumps(body)
    for t in tools:
        Draft202012Validator.check_schema(t.input_schema)
    assert len(operations(SPEC, "http_api", allow_write=True, allowed_paths=[], limit=2)) == 2


def test_swagger2_body_and_slug() -> None:
    spec = {
        "swagger": "2.0",
        "paths": {
            "/items": {
                "post": {
                    "parameters": [
                        {"name": "payload", "in": "body", "schema": {"type": "object"}},
                        {"name": "file", "in": "formData", "type": "file"},
                    ]
                }
            }
        },
    }
    (tool,) = operations(spec, "http_api", allow_write=True, allowed_paths=[], limit=5)
    assert tool.name == "http_api.post_items" and "body" in tool.input_schema["properties"]
    assert slug("getUserByID") == "get_user_by_id" and slug("123go") == "op_123go"
    with pytest.raises(ValueError):
        operations({"foo": 1}, "http_api", allow_write=False, allowed_paths=[], limit=5)


# ---------------------------------------------------------------- plugin + mock API
class FakeApi:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.rstrip("/")
        if path.endswith("/openapi.json"):
            return httpx.Response(200, json=SPEC)
        if request.url.host == "docs.other.vn":
            return httpx.Response(200, json=SPEC)
        if path.endswith("/orders/7") and request.method == "GET":
            return httpx.Response(200, json={"id": 7, "echo": TOKEN})
        if path.endswith("/moved"):
            return httpx.Response(302, headers={"location": "https://evil.com/x"})
        if path.endswith("/big"):
            return httpx.Response(
                200, content=b"x" * 2_000_000, headers={"content-type": "text/plain"}
            )
        if path.endswith("/locked"):
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(404, json={"error": "not found"})


def conn(**config: Any) -> ConnectionContext:
    cfg = {"base_url": BASE, "auth_type": "bearer", **config}
    return ConnectionContext("c1", "ERP", "http_api", cfg, {"credential": TOKEN})


def ctx(c: ConnectionContext, tool: ToolSpec) -> ToolContext:
    return ToolContext("t1", "u1", "a1", "r1", connection=c, tool=tool)


async def test_discover_and_call_operation() -> None:
    api = FakeApi()
    plugin = HttpApiConnector(transport=httpx.MockTransport(api))
    c = conn(openapi_url="/openapi.json")
    tools = {t.name: t for t in await plugin.discover_tools(c)}
    assert set(tools) == {
        "http_api.get",
        "http_api.list_orders",
        "http_api.get_2",
        "http_api.list_users",
    }
    assert api.requests[0].headers["authorization"] == f"Bearer {TOKEN}"

    result = await plugin.call_tool("http_api.get_2", {"id": 7}, ctx(c, tools["http_api.get_2"]))
    assert not result.is_error
    assert result.content.startswith("HTTP 200") and '"id": 7' in result.content
    assert TOKEN not in result.content and "***" in result.content  # API vọng lại token

    result = await plugin.call_tool(
        "http_api.list_orders", {"status": "new"}, ctx(c, tools["http_api.list_orders"])
    )
    assert api.requests[-1].url.params["status"] == "new" and result.is_error  # 404


async def test_spec_on_other_host_gets_no_credential() -> None:
    api = FakeApi()
    plugin = HttpApiConnector(transport=httpx.MockTransport(api))
    c = conn(openapi_url="https://docs.other.vn/erp.json")
    assert [e.host for e in plugin.network_targets(c.config)] == ["erp.corp.vn", "docs.other.vn"]
    await plugin.discover_tools(c)
    assert "authorization" not in api.requests[0].headers


async def test_generic_tools_guards() -> None:
    api = FakeApi()
    plugin = HttpApiConnector(transport=httpx.MockTransport(api))
    c = conn(allowed_paths=["/orders", "/moved", "/big"])
    tools = {t.name: t for t in await plugin.discover_tools(c)}
    assert set(tools) == {"http_api.get"}  # chưa bật ghi → không có send
    get = tools["http_api.get"]

    r = await plugin.call_tool("http_api.get", {"path": "/admin/users"}, ctx(c, get))
    assert r.is_error and "phạm vi" in r.content
    r = await plugin.call_tool("http_api.get", {"path": "/moved"}, ctx(c, get))
    assert r.is_error and "không tự theo" in r.content and len(api.requests) == 1
    r = await plugin.call_tool("http_api.get", {"path": "/big"}, ctx(c, get))
    assert r.is_error and "lớn hơn" in r.content

    # Tool ghi lưu từ trước nhưng cấu hình đã tắt ghi → vẫn bị chặn lúc gọi
    write = await plugin.discover_tools(conn(allow_write=True))
    send = next(t for t in write if t.name == "http_api.send")
    assert send.risk is RiskLevel.DESTRUCTIVE
    r = await plugin.call_tool(
        "http_api.send", {"method": "POST", "path": "/orders", "body": {}}, ctx(c, send)
    )
    assert r.is_error and "chỉ đọc" in r.content


@pytest.mark.parametrize(
    ("config", "check"),
    [
        ({"auth_type": "basic", "username": "bot"}, "basic"),
        ({"auth_type": "api_key_header", "auth_name": "X-Key"}, "header"),
        ({"auth_type": "api_key_query", "auth_name": "key"}, "query"),
    ],
)
async def test_auth_types(config: dict[str, Any], check: str) -> None:
    api = FakeApi()
    plugin = HttpApiConnector(transport=httpx.MockTransport(api))
    c = conn(**config)
    (get,) = await plugin.discover_tools(c)
    await plugin.call_tool("http_api.get", {"path": "/orders/7"}, ctx(c, get))
    req = api.requests[-1]
    if check == "basic":
        assert (
            req.headers["authorization"]
            == "Basic " + base64.b64encode(f"bot:{TOKEN}".encode()).decode()
        )
    elif check == "header":
        assert req.headers["x-key"] == TOKEN
    else:
        assert req.url.params["key"] == TOKEN


async def test_test_connection() -> None:
    plugin = HttpApiConnector(transport=httpx.MockTransport(FakeApi()))
    ok = await plugin.test_connection(conn(openapi_url="/openapi.json"))
    assert ok.healthy and "ERP 2.1" in ok.detail
    bad = await plugin.test_connection(conn(base_url=BASE + "/locked"))
    assert not bad.healthy and "401" in bad.detail
    missing = ConnectionContext("c", "x", "http_api", {"base_url": BASE, "auth_type": "bearer"}, {})
    assert "credential" in (await plugin.test_connection(missing)).detail


# ---------------------------------------------------------------- qua ConnectionService
async def test_service_creates_without_credential_and_syncs() -> None:
    plugin = HttpApiConnector(transport=httpx.MockTransport(FakeApi()))
    group = plugin.manifest.entry_point_group  # chỉ plugin test (không lấy entry point thật)
    plugins = PluginRegistry(
        source=lambda g: [_StaticEntryPoint("http_api", g, plugin)] if g == group else []
    )
    plugins.load_all()
    svc = ConnectionService(
        InMemoryConnectionRepository(),
        InMemoryVault(),
        PluginConnectorLookup(plugins),
        AuditService(InMemoryAuditLog()),
    )
    builder = Principal(
        subject="b1", tenant_id="t1", username=None, email=None, roles=frozenset({Role.BUILDER})
    )
    c = await svc.create(
        builder,
        name="ERP công khai",
        connector_id="http_api",
        scope=ConnectionScope.PERSONAL,
        config={"base_url": BASE, "openapi_url": "/openapi.json", "allowed_paths": ["/orders"]},
        secrets={},
    )
    tested = await svc.test(builder, c.id)
    assert tested.last_test_ok, tested.last_test_note
    names = [t.name for t in svc.tools_of(tested)]
    assert names == ["http_api.get", "http_api.list_orders", "http_api.get_2"]
    assert all(t.untrusted_output for t in tested.tools)


# ---------------------------------------------------------------- OAuth2 client credentials
class TokenApi:
    def __init__(self) -> None:
        self.issued = 0
        self.valid = ""
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "idp.corp.vn":
            form = dict(x.split("=") for x in request.content.decode().split("&"))
            assert form == {"grant_type": "client_credentials", "scope": "orders.read"}
            expected = "Basic " + base64.b64encode(f"aries:{TOKEN}".encode()).decode()
            if request.headers.get("authorization") != expected:
                return httpx.Response(401, json={"error": "invalid_client"})
            self.issued += 1
            self.valid = f"access-{self.issued}-xyz"
            return httpx.Response(200, json={"access_token": self.valid, "expires_in": 3600})
        if request.headers.get("authorization") != f"Bearer {self.valid}":
            return httpx.Response(401, json={"error": "token expired"})
        return httpx.Response(200, json={"you_sent": request.headers["authorization"]})


async def test_oauth2_client_credentials() -> None:
    api = TokenApi()
    plugin = HttpApiConnector(transport=httpx.MockTransport(api))
    c = conn(
        auth_type="oauth2_client_credentials",
        token_url="https://idp.corp.vn/oauth/token",
        client_id="aries",
        oauth_scope="orders.read",
    )
    assert [e.host for e in plugin.network_targets(c.config)] == ["erp.corp.vn", "idp.corp.vn"]
    (get,) = await plugin.discover_tools(c)
    r1 = await plugin.call_tool("http_api.get", {"path": "/orders"}, ctx(c, get))
    r2 = await plugin.call_tool("http_api.get", {"path": "/orders"}, ctx(c, get))
    assert not r1.is_error and not r2.is_error and api.issued == 1  # token được cache
    assert "access-1" not in r1.content and "***" in r1.content  # token vọng lại bị che

    api.valid = "revoked"  # máy chủ thu hồi token sớm → 401 → lấy token mới, thử lại 1 lần
    r3 = await plugin.call_tool("http_api.get", {"path": "/orders"}, ctx(c, get))
    assert not r3.is_error and api.issued == 2

    wrong = ConnectionContext("c2", "x", "http_api", c.config, {"credential": "sai"})
    health = await plugin.test_connection(wrong)
    assert not health.healthy and "token" in health.detail
    missing = conn(auth_type="oauth2_client_credentials")
    assert "token_url" in (await plugin.test_connection(missing)).detail


# ---------------------------------------------------------------- body form / multipart / text
class EchoApi:
    def __init__(self) -> None:
        self.last: httpx.Request | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.last = request
        return httpx.Response(201, json={"ok": True})


async def test_send_body_types() -> None:
    api = EchoApi()
    plugin = HttpApiConnector(transport=httpx.MockTransport(api))
    c = conn(allow_write=True)
    send = next(t for t in await plugin.discover_tools(c) if t.name == "http_api.send")
    Draft202012Validator.check_schema(send.input_schema)

    async def call(args: dict[str, Any]) -> Any:
        return await plugin.call_tool(
            "http_api.send", {"method": "POST", "path": "/x", **args}, ctx(c, send)
        )

    await call({"body_type": "form", "body": {"a": 1, "b": [1, 2], "ok": True}})
    assert api.last is not None
    assert api.last.headers["content-type"] == "application/x-www-form-urlencoded"
    assert api.last.content == b"a=1&b=1&b=2&ok=true"

    await call({"body_type": "text", "body": "xin chào"})
    assert api.last.headers["content-type"].startswith("text/plain")
    assert api.last.content.decode() == "xin chào"

    r = await call(
        {
            "body_type": "multipart",
            "body": {"note": "báo cáo"},
            "files": [
                {
                    "field": "report",
                    "filename": "a.csv",
                    "content_type": "text/csv",
                    "text": "id,qty\n1,2",
                },
                {"filename": "b.bin", "base64": base64.b64encode(b"\x00\x01").decode()},
            ],
        }
    )
    assert not r.is_error, r.content
    raw = api.last.content
    assert api.last.headers["content-type"].startswith("multipart/form-data")
    assert b'name="note"' in raw and "báo cáo".encode() in raw
    assert b'name="report"; filename="a.csv"' in raw and b"id,qty\n1,2" in raw
    assert b'name="file"; filename="b.bin"' in raw and b"\x00\x01" in raw

    bad = await call({"body_type": "multipart", "files": [{"filename": "x", "base64": "@@@"}]})
    assert bad.is_error and "base64" in bad.content
    big = await call(
        {"body_type": "multipart", "files": [{"filename": "big", "text": "x" * 5_000_001}]}
    )
    assert big.is_error and "MB" in big.content
    wrong = await call({"body_type": "json", "files": [{"filename": "x", "text": "1"}]})
    assert wrong.is_error and "multipart" in wrong.content


async def test_openapi_multipart_operation() -> None:
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/docs": {
                "post": {
                    "operationId": "uploadDoc",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "file": {"type": "string", "format": "binary"},
                                        "tag": {"type": "string"},
                                    },
                                }
                            }
                        }
                    },
                }
            },
            "/login": {
                "post": {
                    "operationId": "login",
                    "requestBody": {
                        "content": {
                            "application/x-www-form-urlencoded": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"user": {"type": "string"}},
                                }
                            }
                        }
                    },
                }
            },
        },
    }
    tools = {
        t.name: t for t in operations(spec, "http_api", allow_write=True, allowed_paths=[], limit=9)
    }
    up = tools["http_api.upload_doc"]
    assert up.binding["body"] == "multipart"
    assert up.input_schema["properties"]["body"]["properties"]["file"]["required"] == ["filename"]
    assert tools["http_api.login"].binding["body"] == "form"

    api = EchoApi()
    plugin = HttpApiConnector(transport=httpx.MockTransport(api))
    c = conn(allow_write=True)
    r = await plugin.call_tool(
        up.name,
        {"body": {"tag": "hd", "file": {"filename": "hd.txt", "text": "nội dung"}}},
        ctx(c, up),
    )
    assert not r.is_error and api.last is not None
    assert (
        b'name="file"; filename="hd.txt"' in api.last.content and b'name="tag"' in api.last.content
    )
