"""Connector PostgreSQL (ADR-0016).

- Mỗi lần gọi tool mở một kết nối ngắn bằng secret do Credential Broker cấp.
- Đọc: transaction READ ONLY + statement_timeout + LIMIT; SQL qua guard (sqlglot).
- Ghi (`db.execute`): chỉ khi config `allow_write`; risk destructive → luôn cần Admin/Owner duyệt.
- Kết quả truy vấn là dữ liệu không tin cậy (có thể chứa prompt injection) → core gắn taint.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    ConnectionContext,
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
from aiagent_plugins.connector_postgres.guard import (
    Scope,
    SqlRejectedError,
    check_select,
    check_write,
    limited,
)

logger = logging.getLogger(__name__)

MAX_CELL_CHARS = 500
CONNECT_TIMEOUT_S = 10.0

_IDENT = {"type": "string", "pattern": r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"}

_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "host": {"type": "string", "minLength": 1, "maxLength": 253, "title": "Máy chủ"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535, "default": 5432},
        "database": {"type": "string", "minLength": 1, "maxLength": 63, "title": "Database"},
        "user": {"type": "string", "minLength": 1, "maxLength": 63, "title": "Người dùng"},
        "sslmode": {
            "type": "string",
            "enum": ["disable", "prefer", "require", "verify-full"],
            "default": "prefer",
        },
        "allowed_schemas": {
            "type": "array",
            "items": _IDENT,
            "minItems": 1,
            "maxItems": 20,
            "default": ["public"],
            "title": "Schema được phép",
        },
        "allowed_tables": {
            "type": "array",
            "maxItems": 200,
            "items": {
                "type": "string",
                "pattern": r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$",
            },
            "default": [],
            "title": "Bảng được phép (schema.bảng; trống = tất cả)",
        },
        "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
        "statement_timeout_ms": {
            "type": "integer",
            "minimum": 100,
            "maximum": 60000,
            "default": 10000,
        },
        "allow_write": {"type": "boolean", "default": False, "title": "Cho phép ghi (cần duyệt)"},
    },
    "required": ["host", "database", "user"],
    "additionalProperties": False,
}

_TABLE_ARG = {"type": "string", "maxLength": 130, "description": "Tên bảng, dạng schema.bảng"}

_MANIFEST = PluginManifest(
    id="postgres",
    kind=PluginKind.CONNECTOR,
    name="PostgreSQL",
    description="Truy vấn CSDL PostgreSQL: liệt kê bảng, xem cấu trúc, chạy SELECT (chỉ đọc).",
    version="1.0.0",
    api_version=PLUGIN_API_VERSION,
    config_schema=_CONFIG_SCHEMA,
    secrets=("password",),
    tools=(
        ToolSpec(
            name="db.list_tables",
            description="Liệt kê các bảng/view được phép truy cập (schema.bảng).",
            risk=RiskLevel.READ,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="db.describe_table",
            description="Xem các cột (tên, kiểu, cho phép NULL) của một bảng.",
            risk=RiskLevel.READ,
            input_schema={
                "type": "object",
                "properties": {"table": _TABLE_ARG},
                "required": ["table"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="db.query",
            description=(
                "Chạy MỘT câu SELECT (PostgreSQL) chỉ đọc; kết quả bị giới hạn số dòng. "
                "Luôn ghi rõ schema.bảng. Dùng db.describe_table trước nếu chưa biết cột."
            ),
            risk=RiskLevel.READ,
            untrusted_output=True,
            input_schema={
                "type": "object",
                "properties": {"sql": {"type": "string", "minLength": 1, "maxLength": 20000}},
                "required": ["sql"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="db.execute",
            description=(
                "Chạy MỘT câu INSERT/UPDATE/DELETE (UPDATE/DELETE phải có WHERE). "
                "Chỉ dùng khi người dùng yêu cầu rõ ràng; luôn cần Admin duyệt."
            ),
            risk=RiskLevel.DESTRUCTIVE,
            input_schema={
                "type": "object",
                "properties": {"sql": {"type": "string", "minLength": 1, "maxLength": 20000}},
                "required": ["sql"],
                "additionalProperties": False,
            },
        ),
    ),
    ui_hints={"icon": "database", "category": "data"},
)


def _scope(config: dict[str, Any]) -> Scope:
    schemas = tuple(s.lower() for s in config.get("allowed_schemas") or ["public"])
    tables = frozenset(t.lower() for t in config.get("allowed_tables") or [])
    return Scope(schemas=schemas, tables=tables)


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    text = str(value)
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + "…"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class PostgresConnector(Plugin):
    @property
    def manifest(self) -> PluginManifest:
        return _MANIFEST

    @asynccontextmanager
    async def _connect(
        self, conn: ConnectionContext, *, readonly: bool
    ) -> AsyncIterator[asyncpg.Connection]:
        cfg = conn.config
        try:
            db = await asyncpg.connect(
                host=cfg["host"],
                port=int(cfg.get("port", 5432)),
                user=cfg["user"],
                database=cfg["database"],
                password=conn.secrets.get("password"),
                ssl=cfg.get("sslmode", "prefer"),
                timeout=CONNECT_TIMEOUT_S,
                server_settings={"application_name": "aries-agent"},
            )
        except (OSError, TimeoutError) as exc:
            raise ConnectionError("Không kết nối được tới máy chủ CSDL.") from exc
        try:
            async with db.transaction(readonly=readonly, isolation="repeatable_read"):
                timeout_ms = int(cfg.get("statement_timeout_ms", 10000))
                await db.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                scope = _scope(cfg)
                path = ", ".join(_quote_ident(s) for s in scope.schemas)
                await db.execute(f"SET LOCAL search_path = {path}")
                yield db
        finally:
            await db.close()

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        conn = ctx.connection
        if conn is None:
            return ToolResult("Tool này cần một kết nối PostgreSQL.", is_error=True)
        try:
            if name == "db.list_tables":
                return await self._list_tables(conn)
            if name == "db.describe_table":
                return await self._describe(conn, str(arguments["table"]))
            if name == "db.query":
                return await self._query(conn, str(arguments["sql"]))
            if name == "db.execute":
                return await self._execute(conn, str(arguments["sql"]))
        except SqlRejectedError as exc:
            return ToolResult(f"SQL bị từ chối: {exc}", is_error=True)
        except ConnectionError as exc:
            return ToolResult(str(exc), is_error=True)
        except asyncpg.QueryCanceledError:
            return ToolResult("Truy vấn quá thời gian cho phép.", is_error=True)
        except asyncpg.PostgresError as exc:
            return ToolResult(f"Lỗi PostgreSQL: {str(exc)[:300]}", is_error=True)
        raise ToolNotFoundError(name)

    async def _list_tables(self, conn: ConnectionContext) -> ToolResult:
        scope = _scope(conn.config)
        async with self._connect(conn, readonly=True) as db:
            rows = await db.fetch(
                "SELECT table_schema, table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = ANY($1::text[]) ORDER BY 1, 2 LIMIT 500",
                list(scope.schemas),
            )
        items = [
            f"{r['table_schema']}.{r['table_name']}"
            + (" (view)" if r["table_type"] == "VIEW" else "")
            for r in rows
            if scope.allows(r["table_schema"], r["table_name"])
        ]
        return ToolResult("\n".join(items) or "(không có bảng nào trong phạm vi được phép)")

    async def _describe(self, conn: ConnectionContext, table: str) -> ToolResult:
        scope = _scope(conn.config)
        schema, _, name = table.lower().rpartition(".")
        schema = schema or scope.default_schema
        if not scope.allows(schema, name):
            raise SqlRejectedError(f"Bảng '{schema}.{name}' không nằm trong phạm vi được phép.")
        async with self._connect(conn, readonly=True) as db:
            rows = await db.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position",
                schema,
                name,
            )
        if not rows:
            return ToolResult(f"Không tìm thấy bảng {schema}.{name}.", is_error=True)
        lines = [
            f"- {r['column_name']}: {r['data_type']}"
            + ("" if r["is_nullable"] == "YES" else " NOT NULL")
            for r in rows
        ]
        return ToolResult(f"Bảng {schema}.{name}:\n" + "\n".join(lines))

    async def _query(self, conn: ConnectionContext, sql: str) -> ToolResult:
        tree = check_select(sql, _scope(conn.config))
        max_rows = int(conn.config.get("max_rows", 200))
        async with self._connect(conn, readonly=True) as db:
            stmt = await db.prepare(limited(tree, max_rows))
            columns = [a.name for a in stmt.get_attributes()]
            records = await stmt.fetch()
        truncated = len(records) > max_rows
        rows = [[_cell(v) for v in r.values()] for r in records[:max_rows]]
        payload = {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}
        return ToolResult(
            json.dumps(payload, ensure_ascii=False, default=str),
            data={"row_count": len(rows), "truncated": truncated},
        )

    async def _execute(self, conn: ConnectionContext, sql: str) -> ToolResult:
        if not conn.config.get("allow_write", False):
            return ToolResult(
                "Kết nối này chỉ đọc (allow_write = false); không thực hiện thao tác ghi.",
                is_error=True,
            )
        tree = check_write(sql, _scope(conn.config))
        async with self._connect(conn, readonly=False) as db:
            status = await db.execute(tree.sql(dialect="postgres"))
        return ToolResult(f"Đã thực hiện: {status}")

    async def test_connection(self, connection: ConnectionContext) -> HealthStatus:
        try:
            async with self._connect(connection, readonly=True) as db:
                version = await db.fetchval("SHOW server_version")
                can_write = await db.fetchval(
                    "SELECT bool_or(has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE')) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = ANY($1::text[]) AND c.relkind IN ('r','p')",
                    list(_scope(connection.config).schemas),
                )
        except ConnectionError as exc:
            return HealthStatus(False, str(exc))
        except asyncpg.InvalidPasswordError:
            return HealthStatus(False, "Sai tên đăng nhập hoặc mật khẩu.")
        except asyncpg.PostgresError as exc:
            return HealthStatus(False, f"Lỗi PostgreSQL: {str(exc)[:200]}")
        note = f"Kết nối OK — PostgreSQL {version}."
        if can_write:
            note += " Cảnh báo: role này có quyền ghi; nên dùng role chỉ-đọc."
        return HealthStatus(True, note)
