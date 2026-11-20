"""SQL guard cho connector Postgres (ADR-0016) — lớp phòng thủ trước quyền của role DB.

Không thay thế nguyên tắc chính: connection nên dùng role DB chỉ-đọc, chỉ được GRANT
trên đúng các bảng cần thiết. Guard chặn các đường vòng phổ biến khi model sinh SQL.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Hàm có tác dụng phụ, đọc file/hệ thống, hoặc chạy SQL dạng chuỗi (lách allowlist bảng).
_BLOCKED_FUNC_PREFIXES = (
    "pg_",
    "lo_",
    "dblink",
    "query_to_",
    "table_to_",
    "cursor_to_",
    "schema_to_",
    "database_to_",
    "txid_",
    "set_config",
    "nextval",
    "setval",
    "currval",
    "lastval",
)
_WRITE_STATEMENTS = (exp.Insert, exp.Update, exp.Delete)
_DDL_OR_ADMIN = (
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Command,
    exp.Copy,
    exp.Grant,
    exp.TruncateTable,
    exp.Merge,
    exp.Set,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Use,
    exp.Pragma,
)


class SqlRejectedError(ValueError):
    """SQL bị từ chối — thông điệp an toàn để trả về model."""


@dataclass(frozen=True)
class Scope:
    schemas: tuple[str, ...]  # schema được phép; schema đầu tiên = mặc định cho tên không qualify
    tables: frozenset[str]  # "schema.table"; rỗng = mọi bảng trong các schema được phép

    @property
    def default_schema(self) -> str:
        return self.schemas[0]

    def allows(self, schema: str, table: str) -> bool:
        schema, table = schema.lower(), table.lower()
        if schema not in self.schemas:
            return False
        return not self.tables or f"{schema}.{table}" in self.tables


def _parse_one(sql: str) -> exp.Expr:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SqlRejectedError("SQL trống.")
    if len(sql) > 20_000:
        raise SqlRejectedError("SQL quá dài.")
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except ParseError as exc:
        raise SqlRejectedError(
            f"Không phân tích được SQL: {str(exc).splitlines()[0][:200]}"
        ) from exc
    parsed = [s for s in statements if s is not None]
    if len(parsed) != 1:
        raise SqlRejectedError("Chỉ cho phép đúng MỘT câu lệnh.")
    return parsed[0]


def _func_name(node: exp.Func) -> str:
    return (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()


def _check_tables(tree: exp.Expr, scope: Scope) -> list[str]:
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    used: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name:
            raise SqlRejectedError("Nguồn dữ liệu không được hỗ trợ (chỉ đọc từ bảng/view).")
        if table.args.get("catalog"):
            raise SqlRejectedError("Không được truy cập database khác.")
        schema = (table.db or "").lower()
        if not schema:
            if name in cte_names:
                continue
            if name.startswith("pg_"):
                raise SqlRejectedError("Không được truy cập catalog hệ thống.")
            schema = scope.default_schema
        if not scope.allows(schema, name):
            raise SqlRejectedError(f"Bảng '{schema}.{name}' không nằm trong phạm vi được phép.")
        used.append(f"{schema}.{name}")
    return used


def _check_common(tree: exp.Expr) -> None:
    for node in tree.walk():
        if isinstance(node, _DDL_OR_ADMIN):
            raise SqlRejectedError("Không cho phép lệnh DDL/quản trị.")
        if isinstance(node, exp.Func) and _func_name(node).startswith(_BLOCKED_FUNC_PREFIXES):
            raise SqlRejectedError(f"Hàm '{_func_name(node)}' không được phép.")


def check_select(sql: str, scope: Scope) -> exp.Expr:
    """Chỉ SELECT/UNION/… thuần đọc; mọi bảng nằm trong phạm vi."""
    tree = _parse_one(sql)
    if not isinstance(tree, exp.Query):
        raise SqlRejectedError("db.query chỉ nhận câu SELECT.")
    _check_common(tree)
    for node in tree.walk():
        if isinstance(node, _WRITE_STATEMENTS):
            raise SqlRejectedError("db.query không được chứa INSERT/UPDATE/DELETE.")
        if isinstance(node, exp.Into):
            raise SqlRejectedError("Không cho phép SELECT INTO.")
        if isinstance(node, exp.Lock):
            raise SqlRejectedError("Không cho phép FOR UPDATE/SHARE.")
    _check_tables(tree, scope)
    return tree


def check_write(sql: str, scope: Scope) -> exp.Expr:
    """Một INSERT/UPDATE/DELETE; UPDATE/DELETE bắt buộc có WHERE."""
    tree = _parse_one(sql)
    if not isinstance(tree, _WRITE_STATEMENTS):
        raise SqlRejectedError("db.execute chỉ nhận INSERT, UPDATE hoặc DELETE.")
    _check_common(tree)
    if isinstance(tree, exp.Update | exp.Delete) and tree.args.get("where") is None:
        raise SqlRejectedError("UPDATE/DELETE bắt buộc có WHERE.")
    nested = [n for n in tree.walk() if isinstance(n, _WRITE_STATEMENTS) and n is not tree]
    if nested:
        raise SqlRejectedError("Không cho phép câu lệnh ghi lồng nhau.")
    _check_tables(tree, scope)
    return tree


def limited(tree: exp.Expr, max_rows: int) -> str:
    """Bọc truy vấn để giới hạn số dòng (lấy dư 1 dòng để biết có bị cắt không)."""
    inner = tree.sql(dialect="postgres")  # đã qua guard; LIMIT là số nguyên
    return f"SELECT * FROM ({inner}) AS _aries_q LIMIT {int(max_rows) + 1}"  # noqa: S608  # nosec B608
