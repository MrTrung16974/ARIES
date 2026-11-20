from __future__ import annotations

import pytest

from aiagent.plugin_sdk import ConnectionContext, ToolContext
from aiagent_plugins.connector_postgres import PostgresConnector
from aiagent_plugins.connector_postgres.guard import (
    Scope,
    SqlRejectedError,
    check_select,
    check_write,
    limited,
)

SCOPE = Scope(schemas=("public", "sales"), tables=frozenset())
NARROW = Scope(schemas=("sales",), tables=frozenset({"sales.orders"}))


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders",
        "select o.id, c.name from sales.orders o join public.customers c on c.id = o.cid",
        "with x as (select id from orders) select count(*) from x",
        "select id from orders union all select id from sales.refunds;",
        "select now(), lower(name), coalesce(a, 0) from customers where id = 1",
    ],
)
def test_select_allowed(sql: str) -> None:
    check_select(sql, SCOPE)


@pytest.mark.parametrize(
    ("sql", "fragment"),
    [
        ("select 1; select 2", "MỘT"),
        ("delete from orders where id = 1", "SELECT"),
        ("with x as (delete from orders returning *) select * from x", "INSERT/UPDATE/DELETE"),
        ("select * into backup from orders", "SELECT INTO"),
        ("select * from orders for update", "FOR UPDATE"),
        ("select pg_sleep(10)", "pg_sleep"),
        ("select pg_read_file('/etc/passwd')", "pg_read_file"),
        ("select query_to_xml('delete from orders', true, true, '')", "query_to_xml"),
        ("select set_config('role', 'postgres', true)", "set_config"),
        ("select nextval('s')", "nextval"),
        ("select * from pg_authid", "catalog"),
        ("select * from pg_catalog.pg_authid", "phạm vi"),
        ("select * from information_schema.tables", "phạm vi"),
        ("select * from other.secret", "phạm vi"),
        ("select * from otherdb.public.t", "database khác"),
        ("drop table orders", "SELECT"),
        ("copy orders to '/tmp/x'", "SELECT"),
        ("", "trống"),
        ("selec * fro", ""),
    ],
)
def test_select_rejected(sql: str, fragment: str) -> None:
    with pytest.raises(SqlRejectedError) as err:
        check_select(sql, SCOPE)
    assert fragment in str(err.value)


def test_table_allowlist() -> None:
    check_select("select * from orders", NARROW)  # mặc định schema đầu tiên = sales
    with pytest.raises(SqlRejectedError):
        check_select("select * from sales.customers", NARROW)


def test_write_guard() -> None:
    check_write("update orders set status = 'x' where id = 1", SCOPE)
    check_write("insert into sales.orders (id) values (1)", SCOPE)
    for bad in (
        "delete from orders",
        "update orders set a = 1",
        "truncate orders",
        "select 1",
        "insert into orders select * from other.t",
        "delete from orders where id in (select pg_sleep(1))",
    ):
        with pytest.raises(SqlRejectedError):
            check_write(bad, SCOPE)


def test_limited_wraps_query() -> None:
    tree = check_select("select id from orders order by id", SCOPE)
    assert limited(tree, 50) == (
        "SELECT * FROM (SELECT id FROM orders ORDER BY id) AS _aries_q LIMIT 51"
    )


async def test_connector_requires_connection_and_write_flag() -> None:
    plugin = PostgresConnector()
    ctx = ToolContext(tenant_id="t", user_id="u", agent_id="a", run_id="r")
    r = await plugin.call_tool("db.query", {"sql": "select 1"}, ctx)
    assert r.is_error
    conn = ConnectionContext(
        id="c",
        name="x",
        connector_id="postgres",
        config={"host": "h", "database": "d", "user": "u"},
        secrets={"password": "s3cr3t-value"},
    )
    ctx2 = ToolContext(tenant_id="t", user_id="u", agent_id="a", run_id="r", connection=conn)
    r = await plugin.call_tool("db.execute", {"sql": "delete from t where id=1"}, ctx2)
    assert r.is_error and "chỉ đọc" in r.content
    r = await plugin.call_tool("db.query", {"sql": "select * from pg_authid"}, ctx2)
    assert r.is_error and "từ chối" in r.content
    assert "s3cr3t-value" not in repr(conn)
