"""Connector Redis, Kafka, gRPC (ADR-0023)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from concurrent import futures
from typing import Any

import grpc
import pytest
from fakeredis import FakeServer
from fakeredis.aioredis import FakeRedis
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from grpc_reflection.v1alpha import reflection
from jsonschema import Draft202012Validator

from aiagent.plugin_sdk import ConnectionContext, RiskLevel, ToolContext, ToolSpec
from aiagent_plugins.connector_grpc import GrpcConnector, message_schema, method_risk
from aiagent_plugins.connector_kafka import (
    KafkaConnector,
    KafkaMessage,
    client_options,
    topic_allowed,
)
from aiagent_plugins.connector_redis import RedisConnector, scan_patterns


def ctx(conn: ConnectionContext, tool: ToolSpec | None = None) -> ToolContext:
    return ToolContext("t1", "u1", "a1", "r1", connection=conn, tool=tool)


# ================================================================ Redis
@pytest.fixture
def redis_plugin() -> RedisConnector:
    server = FakeServer()
    return RedisConnector(client_factory=lambda conn, tls: FakeRedis(server=server))


def rconn(**cfg: Any) -> ConnectionContext:
    config = {"host": "cache.corp.vn", "key_prefixes": ["app:"], "max_items": 3, **cfg}
    return ConnectionContext("c1", "Cache", "redis", config, {})


async def seed(plugin: RedisConnector, conn: ConnectionContext) -> None:
    async with plugin._client(conn) as r:
        await r.set("app:greeting", "xin chào", ex=100)
        await r.hset("app:user:1", mapping={"name": "An", "city": "HN"})
        await r.rpush("app:queue", "a", "b", "c", "d", "e")
        await r.zadd("app:rank", {"x": 1, "y": 2})
        await r.xadd("app:events", {"type": "login"})
        await r.set("secret:token", "do-not-read")


def test_redis_scan_patterns() -> None:
    assert scan_patterns("", ["app:", "a*b"]) == ["app:*", "a\\*b*"]
    assert scan_patterns("app:user:*", ["app:"]) == ["app:user:*"]
    for bad in ("*", "secret:*", "ap*", "[a]pp:*"):
        with pytest.raises(ValueError):
            scan_patterns(bad, ["app:"])


async def test_redis_read_tools(redis_plugin: RedisConnector) -> None:
    conn = rconn(max_items=10)
    await seed(redis_plugin, conn)
    r = await redis_plugin.call_tool("redis.scan", {}, ctx(conn))
    keys = json.loads(r.content)["keys"]
    assert "secret:token" not in keys and "app:user:1" in keys

    got = json.loads(
        (await redis_plugin.call_tool("redis.get", {"key": "app:greeting"}, ctx(conn))).content
    )
    assert got["type"] == "string" and got["value"] == "xin chào" and 0 < got["ttl_s"] <= 100
    got = json.loads(
        (await redis_plugin.call_tool("redis.get", {"key": "app:user:1"}, ctx(conn))).content
    )
    assert got["value"] == {"name": "An", "city": "HN"}
    small = rconn(max_items=3)
    got = json.loads(
        (await redis_plugin.call_tool("redis.get", {"key": "app:queue"}, ctx(small))).content
    )
    assert got["value"] == ["a", "b", "c"] and got["size"] == 5 and got["truncated"] is True
    got = json.loads(
        (await redis_plugin.call_tool("redis.get", {"key": "app:rank"}, ctx(conn))).content
    )
    assert got["type"] == "zset" and got["value"][0][0] == "x"
    got = json.loads(
        (await redis_plugin.call_tool("redis.get", {"key": "app:events"}, ctx(conn))).content
    )
    assert got["value"][0]["fields"] == {"type": "login"}

    r = await redis_plugin.call_tool("redis.get", {"key": "secret:token"}, ctx(conn))
    assert r.is_error and "ngoài" in r.content
    r = await redis_plugin.call_tool("redis.get", {"key": "app:none"}, ctx(conn))
    assert r.is_error and "không tồn tại" in r.content


async def test_redis_write_needs_allow_write(redis_plugin: RedisConnector) -> None:
    ro = rconn()
    r = await redis_plugin.call_tool("redis.set", {"key": "app:x", "value": "1"}, ctx(ro))
    assert r.is_error and "chỉ đọc" in r.content
    rw = rconn(allow_write=True)
    assert not (
        await redis_plugin.call_tool("redis.set", {"key": "app:x", "value": "1"}, ctx(rw))
    ).is_error
    r = await redis_plugin.call_tool("redis.set", {"key": "other:x", "value": "1"}, ctx(rw))
    assert r.is_error
    r = await redis_plugin.call_tool("redis.delete", {"key": "app:x"}, ctx(rw))
    assert r.content == "Đã xóa 1 key."
    assert (await redis_plugin.test_connection(rw)).healthy
    assert redis_plugin.network_targets(rw.config)[0].port == 6379


# ================================================================ Kafka
class FakeKafka:
    def __init__(self) -> None:
        self.produced: list[tuple[str, bytes | None, bytes]] = []

    async def topics(self, conn: ConnectionContext) -> dict[str, int]:
        return {"orders.created": 3, "orders.paid": 1, "hr.salary": 1}

    async def latest(
        self, conn: ConnectionContext, topic: str, partitions: list[int], limit: int
    ) -> list[KafkaMessage]:
        msgs = [
            KafkaMessage(0, i, 1000 + i, b"k", json.dumps({"n": i}).encode()) for i in range(10)
        ]
        return [m for m in msgs if not partitions or m.partition in partitions][-limit:]

    async def produce(
        self, conn: ConnectionContext, topic: str, key: bytes | None, value: bytes
    ) -> tuple[int, int]:
        self.produced.append((topic, key, value))
        return 0, 42


def kconn(**cfg: Any) -> ConnectionContext:
    config = {
        "bootstrap_servers": ["kafka1.corp.vn:9093", "kafka2.corp.vn"],
        "allowed_topics": ["orders.*"],
        "max_messages": 5,
        **cfg,
    }
    return ConnectionContext("k1", "Kafka", "kafka", config, {"password": "pw"})


async def test_kafka_tools() -> None:
    fake = FakeKafka()
    plugin = KafkaConnector(gateway=fake)
    conn = kconn()
    assert [(e.host, e.port) for e in plugin.network_targets(conn.config)] == [
        ("kafka1.corp.vn", 9093),
        ("kafka2.corp.vn", 9092),
    ]
    topics = json.loads((await plugin.call_tool("kafka.list_topics", {}, ctx(conn))).content)
    assert [t["topic"] for t in topics["topics"]] == ["orders.created", "orders.paid"]

    r = await plugin.call_tool("kafka.peek", {"topic": "orders.created", "limit": 50}, ctx(conn))
    data = json.loads(r.content)
    assert data["count"] == 5 and data["messages"][0]["offset"] == 9  # mới nhất trước
    assert data["messages"][0]["value"] == {"n": 9}
    r = await plugin.call_tool("kafka.peek", {"topic": "hr.salary"}, ctx(conn))
    assert r.is_error and "không nằm trong" in r.content

    r = await plugin.call_tool("kafka.produce", {"topic": "orders.paid", "value": "{}"}, ctx(conn))
    assert r.is_error and fake.produced == []
    rw = kconn(allow_produce=True)
    r = await plugin.call_tool(
        "kafka.produce", {"topic": "orders.paid", "value": "{}", "key": "o1"}, ctx(rw)
    )
    assert "offset 42" in r.content and fake.produced == [("orders.paid", b"o1", b"{}")]
    health = await plugin.test_connection(conn)
    assert health.healthy and "2/3" in health.detail


def test_kafka_options_and_topics() -> None:
    assert topic_allowed("orders.x", ["orders.*"]) and not topic_allowed("ordersx", ["orders"])
    plain = client_options(kconn())
    assert "sasl_mechanism" not in plain and "ssl_context" not in plain
    sasl = client_options(
        kconn(security_protocol="SASL_SSL", sasl_mechanism="SCRAM-SHA-512", username="bot")
    )
    assert sasl["sasl_plain_username"] == "bot" and sasl["sasl_plain_password"] == "pw"
    assert sasl["sasl_mechanism"] == "SCRAM-SHA-512" and sasl["ssl_context"] is not None


# ================================================================ gRPC
F = descriptor_pb2.FieldDescriptorProto


def _shop_pool() -> descriptor_pool.DescriptorPool:
    fdp = descriptor_pb2.FileDescriptorProto(name="shop.proto", package="shop.v1", syntax="proto3")
    enum = fdp.enum_type.add(name="Status")
    enum.value.add(name="NEW", number=0)
    enum.value.add(name="PAID", number=1)
    req = fdp.message_type.add(name="GetOrderRequest")
    req.field.add(name="id", number=1, type=F.TYPE_INT64, label=F.LABEL_OPTIONAL)
    order = fdp.message_type.add(name="Order")
    order.field.add(name="id", number=1, type=F.TYPE_INT64, label=F.LABEL_OPTIONAL)
    order.field.add(name="sku", number=2, type=F.TYPE_STRING, label=F.LABEL_OPTIONAL)
    order.field.add(
        name="status",
        number=3,
        type=F.TYPE_ENUM,
        type_name=".shop.v1.Status",
        label=F.LABEL_OPTIONAL,
    )
    order.field.add(name="tags", number=4, type=F.TYPE_STRING, label=F.LABEL_REPEATED)
    entry = order.nested_type.add(name="AttrsEntry")
    entry.options.map_entry = True
    entry.field.add(name="key", number=1, type=F.TYPE_STRING, label=F.LABEL_OPTIONAL)
    entry.field.add(name="value", number=2, type=F.TYPE_STRING, label=F.LABEL_OPTIONAL)
    order.field.add(
        name="attrs",
        number=5,
        type=F.TYPE_MESSAGE,
        type_name=".shop.v1.Order.AttrsEntry",
        label=F.LABEL_REPEATED,
    )
    svc = fdp.service.add(name="OrderService")
    svc.method.add(
        name="GetOrder", input_type=".shop.v1.GetOrderRequest", output_type=".shop.v1.Order"
    )
    svc.method.add(
        name="CancelOrder", input_type=".shop.v1.GetOrderRequest", output_type=".shop.v1.Order"
    )
    svc.method.add(
        name="Watch",
        input_type=".shop.v1.GetOrderRequest",
        output_type=".shop.v1.Order",
        server_streaming=True,
    )
    svc.method.add(
        name="CountOrders",
        input_type=".shop.v1.GetOrderRequest",
        output_type=".shop.v1.Order",
        client_streaming=True,
    )
    svc.method.add(
        name="BulkGet",
        input_type=".shop.v1.GetOrderRequest",
        output_type=".shop.v1.Order",
        client_streaming=True,
        server_streaming=True,
    )
    admin = fdp.service.add(name="AdminService")
    admin.method.add(
        name="ListUsers", input_type=".shop.v1.GetOrderRequest", output_type=".shop.v1.Order"
    )
    pool = descriptor_pool.DescriptorPool()
    pool.Add(fdp)
    return pool


@pytest.fixture
def grpc_target() -> Iterator[tuple[str, list[list[tuple[str, str]]]]]:
    pool = _shop_pool()
    req_cls = message_factory.GetMessageClass(pool.FindMessageTypeByName("shop.v1.GetOrderRequest"))
    order_cls = message_factory.GetMessageClass(pool.FindMessageTypeByName("shop.v1.Order"))
    seen_metadata: list[list[tuple[str, str]]] = []

    def get_order(request: Any, context: grpc.ServicerContext) -> Any:
        seen_metadata.append([(m.key, m.value) for m in context.invocation_metadata()])
        if request.id == 404:
            context.abort(grpc.StatusCode.NOT_FOUND, "không có đơn")
        return order_cls(id=request.id, sku="A1", status=1, tags=["vip"], attrs={"k": "v"})

    handler = grpc.unary_unary_rpc_method_handler(
        get_order,
        request_deserializer=req_cls.FromString,
        response_serializer=order_cls.SerializeToString,
    )

    def watch(request: Any, context: grpc.ServicerContext) -> Iterator[Any]:
        for i in range(3):
            yield order_cls(id=request.id + i, sku="W")
        while request.id == 999 and context.is_active():  # stream không tự kết thúc
            time.sleep(0.05)

    def count(requests: Iterator[Any], context: grpc.ServicerContext) -> Any:
        return order_cls(id=sum(1 for _ in requests), sku="COUNT")

    def bulk(requests: Iterator[Any], context: grpc.ServicerContext) -> Iterator[Any]:
        for r in requests:
            yield order_cls(id=r.id, sku=f"B{r.id}")

    ser = {
        "request_deserializer": req_cls.FromString,
        "response_serializer": order_cls.SerializeToString,
    }
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "shop.v1.OrderService",
                {
                    "GetOrder": handler,
                    "CancelOrder": handler,
                    "Watch": grpc.unary_stream_rpc_method_handler(watch, **ser),
                    "CountOrders": grpc.stream_unary_rpc_method_handler(count, **ser),
                    "BulkGet": grpc.stream_stream_rpc_method_handler(bulk, **ser),
                },
            ),
        )
    )
    names = ("shop.v1.OrderService", "shop.v1.AdminService", reflection.SERVICE_NAME)
    reflection.enable_server_reflection(names, server, pool=pool)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    yield f"127.0.0.1:{port}", seen_metadata
    server.stop(None)


def gconn(target: str, **cfg: Any) -> ConnectionContext:
    config = {"target": target, "tls": False, "auth_type": "bearer", "timeout_s": 5, **cfg}
    return ConnectionContext("g1", "Shop", "grpc", config, {"credential": "tok"})


def test_grpc_schema_and_risk() -> None:
    order = _shop_pool().FindMessageTypeByName("shop.v1.Order")
    schema = message_schema(order)
    Draft202012Validator.check_schema(schema)
    props = schema["properties"]
    assert props["id"]["type"] == ["integer", "string"]
    assert props["status"]["enum"] == ["NEW", "PAID"]
    assert props["tags"] == {"type": "array", "items": {"type": "string"}}
    assert props["attrs"] == {"type": "object", "additionalProperties": {"type": "string"}}
    assert method_risk("GetOrder") is RiskLevel.READ and method_risk("ListUsers") is RiskLevel.READ
    assert (
        method_risk("CancelOrder") is RiskLevel.WRITE and method_risk("Getaway") is RiskLevel.READ
    )


async def test_grpc_discover_and_call(grpc_target) -> None:  # type: ignore[no-untyped-def]
    target, seen = grpc_target
    plugin = GrpcConnector()
    conn = gconn(target, allowed_services=["shop.v1.OrderService"])
    health = await plugin.test_connection(conn)
    assert health.healthy and "5 method" in health.detail, health.detail

    tools = {t.name: t for t in await plugin.discover_tools(conn)}
    assert set(tools) == {
        "grpc.order_service_get_order",
        "grpc.order_service_cancel_order",
        "grpc.order_service_watch",
        "grpc.order_service_count_orders",
        "grpc.order_service_bulk_get",
    }
    get = tools["grpc.order_service_get_order"]
    assert (
        get.risk is RiskLevel.READ
        and tools["grpc.order_service_cancel_order"].risk is RiskLevel.WRITE
    )
    assert get.binding == {
        "service": "shop.v1.OrderService",
        "method": "GetOrder",
        "client_streaming": False,
        "server_streaming": False,
    }

    r = await plugin.call_tool(get.name, {"id": 7}, ctx(conn, get))
    assert not r.is_error, r.content
    assert json.loads(r.content) == {
        "id": "7",
        "sku": "A1",
        "status": "PAID",
        "tags": ["vip"],
        "attrs": {"k": "v"},
    }
    assert ("authorization", "Bearer tok") in seen[-1]

    r = await plugin.call_tool(get.name, {"id": 404}, ctx(conn, get))
    assert r.is_error and "NOT_FOUND" in r.content
    r = await plugin.call_tool(get.name, {"id": "abc"}, ctx(conn, get))
    assert r.is_error and "protobuf" in r.content

    # Service ngoài phạm vi: tool cũ còn lưu cũng không gọi được
    admin = ToolSpec(
        name="grpc.admin_service_list_users",
        description="x",
        risk=RiskLevel.READ,
        binding={"service": "shop.v1.AdminService", "method": "ListUsers"},
    )
    r = await plugin.call_tool(admin.name, {}, ctx(conn, admin))
    assert r.is_error and "phạm vi" in r.content


async def test_grpc_unreachable() -> None:
    plugin = GrpcConnector()
    health = await plugin.test_connection(gconn("127.0.0.1:1", timeout_s=1))
    assert not health.healthy and "timeout" in health.detail
    missing = ConnectionContext("g", "x", "grpc", {"target": "a:1", "auth_type": "bearer"}, {})
    assert "credential" in (await plugin.test_connection(missing)).detail


# ---------------------------------------------------------------- gRPC qua file .proto
PROTO = """
syntax = "proto3";
package inventory.v1;
import "google/protobuf/timestamp.proto";

message StockRequest { string sku = 1; }
message Stock {
  string sku = 1;
  int32 quantity = 2;
  google.protobuf.Timestamp updated_at = 3;
}
service StockService {
  rpc GetStock(StockRequest) returns (Stock);
  rpc ReserveStock(StockRequest) returns (Stock);
}
"""


@pytest.fixture
def no_reflection_target() -> Iterator[str]:
    fds = GrpcConnector.compile_proto(PROTO)
    pool = descriptor_pool.DescriptorPool()
    for fd in fds.file:
        pool.Add(fd)
    req_cls = message_factory.GetMessageClass(
        pool.FindMessageTypeByName("inventory.v1.StockRequest")
    )
    stock_cls = message_factory.GetMessageClass(pool.FindMessageTypeByName("inventory.v1.Stock"))

    def get_stock(request: Any, context: grpc.ServicerContext) -> Any:
        stock = stock_cls(sku=request.sku, quantity=12)
        stock.updated_at.FromJsonString("2026-11-23T08:00:00Z")
        return stock

    handler = grpc.unary_unary_rpc_method_handler(
        get_stock,
        request_deserializer=req_cls.FromString,
        response_serializer=stock_cls.SerializeToString,
    )
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    server.add_generic_rpc_handlers(
        (grpc.method_handlers_generic_handler("inventory.v1.StockService", {"GetStock": handler}),)
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    yield f"127.0.0.1:{port}"
    server.stop(None)


async def test_grpc_with_proto_source(no_reflection_target: str) -> None:
    plugin = GrpcConnector()
    # Không có reflection và chưa dán .proto → hướng dẫn rõ ràng
    plain = await plugin.test_connection(gconn(no_reflection_target, auth_type="none"))
    assert not plain.healthy and ".proto" in plain.detail

    conn = gconn(no_reflection_target, auth_type="none", proto_source=PROTO)
    health = await plugin.test_connection(conn)
    assert health.healthy and "2 method" in health.detail, health.detail
    tools = {t.name: t for t in await plugin.discover_tools(conn)}
    get = tools["grpc.stock_service_get_stock"]
    assert get.risk is RiskLevel.READ
    assert tools["grpc.stock_service_reserve_stock"].risk is RiskLevel.WRITE
    r = await plugin.call_tool(get.name, {"sku": "A1"}, ctx(conn, get))
    assert json.loads(r.content) == {
        "sku": "A1",
        "quantity": 12,
        "updated_at": "2026-11-23T08:00:00Z",
    }

    bad = await plugin.test_connection(
        gconn(no_reflection_target, auth_type="none", proto_source="syntax = 'proto3'; message {")
    )
    assert not bad.healthy and "không hợp lệ" in bad.detail and "service.proto" in bad.detail


# ---------------------------------------------------------------- Kafka trigger
class FakeKafkaLog(FakeKafka):
    """Log theo partition: message (offset tăng dần, timestamp do producer đặt)."""

    def __init__(self) -> None:
        super().__init__()
        self.log: dict[int, list[KafkaMessage]] = {0: [], 1: []}

    def append(self, partition: int, value: str, ts: int) -> None:
        q = self.log.setdefault(partition, [])
        q.append(KafkaMessage(partition, len(q), ts, None, value.encode()))

    async def read_from(
        self,
        conn: ConnectionContext,
        topic: str,
        starts: dict[int, int] | None,
        max_records: int,
    ) -> tuple[list[KafkaMessage], dict[int, int]]:
        ends = {p: len(q) for p, q in self.log.items()}
        if starts is None:
            return [], ends
        out = [m for p, q in self.log.items() for m in q[starts.get(p, 0) :]]
        return out[:max_records], ends


async def test_kafka_trigger_offsets_filter_and_order() -> None:
    log = FakeKafkaLog()
    plugin = KafkaConnector(gateway=log)
    conn = kconn()
    cfg = {"topic": "orders.created"}
    log.append(0, "cũ", ts=1)

    events, state = await plugin.poll_trigger("kafka.new_message", conn, {}, cfg)
    assert events == [] and state == {"topic": "orders.created", "offsets": {"0": 1, "1": 0}}

    # Timestamp lộn xộn trong partition 0 vẫn giữ đúng thứ tự offset
    log.append(0, "đơn A", ts=50)
    log.append(0, "đơn B", ts=10)
    log.append(1, "đơn C", ts=20)
    events, state = await plugin.poll_trigger("kafka.new_message", conn, state, cfg)
    assert [e.id for e in events] == [
        "kafka:orders.created:1:0",  # ts=20 cũ hơn đầu hàng p0 (ts=50)
        "kafka:orders.created:0:1",
        "kafka:orders.created:0:2",
    ]
    assert events[1].content == "value: đơn A"
    assert state["offsets"] == {"0": 3, "1": 1}
    events, state = await plugin.poll_trigger("kafka.new_message", conn, state, cfg)
    assert events == []

    # Bộ lọc: message không khớp vẫn được tính đã đọc
    log.append(0, "heartbeat", ts=60)
    log.append(0, "đơn VIP", ts=61)
    filtered = {**cfg, "contains": "vip"}
    events, state = await plugin.poll_trigger("kafka.new_message", conn, state, filtered)
    assert [e.content for e in events] == ["value: đơn VIP"] and state["offsets"]["0"] == 5

    # Tối đa 10 sự kiện/lần; phần dư để lần sau, không mất
    for i in range(15):
        log.append(1, f"loạt {i}", ts=100 + i)
    first, state = await plugin.poll_trigger("kafka.new_message", conn, state, cfg)
    second, state = await plugin.poll_trigger("kafka.new_message", conn, state, cfg)
    assert len(first) == 10 and len(second) == 5
    assert [e.content for e in first + second] == [f"value: loạt {i}" for i in range(15)]

    # Đổi topic → ghi mốc lại; topic ngoài phạm vi → lỗi
    events, state = await plugin.poll_trigger(
        "kafka.new_message", conn, state, {"topic": "orders.paid"}
    )
    assert events == [] and state["topic"] == "orders.paid"
    with pytest.raises(ValueError, match="không nằm trong"):
        await plugin.poll_trigger("kafka.new_message", conn, {}, {"topic": "hr.salary"})


# ---------------------------------------------------------------- Redis cluster / sentinel
class _Closable:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _Node:
    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port


async def _dns(host: str, port: int) -> list[str]:
    table = {"redis": ["172.18.0.5"], "r1.corp.vn": ["10.0.0.11"], "s1.corp.vn": ["10.0.0.21"]}
    if host not in table:
        raise OSError(host)
    return table[host]


def _policy() -> Any:
    from aiagent.plugin_sdk import NetworkPolicy

    return NetworkPolicy.build(blocked_hosts=["redis"], resolver=_dns)


def test_redis_config_schema_modes() -> None:
    from aiagent_plugins.connector_redis import _CONFIG_SCHEMA

    v = Draft202012Validator(_CONFIG_SCHEMA)
    base = {"key_prefixes": ["app:"]}
    assert v.is_valid({**base, "host": "r1"})
    assert not v.is_valid(base)  # standalone cần host
    assert not v.is_valid({**base, "mode": "cluster"})
    assert v.is_valid({**base, "mode": "cluster", "nodes": ["r1:6379"]})
    assert not v.is_valid({**base, "mode": "sentinel", "sentinels": ["s1:26379"]})
    assert v.is_valid(
        {**base, "mode": "sentinel", "sentinels": ["s1:26379"], "service_name": "mymaster"}
    )


async def test_redis_cluster_checks_advertised_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiagent_plugins.connector_redis as mod

    created: list[Any] = []

    class FakeCluster(_Closable):
        def __init__(self, startup_nodes: list[Any], **kw: Any) -> None:
            super().__init__()
            self.startup, self.kw = startup_nodes, kw
            self.nodes = [_Node("r1.corp.vn", 6379), _Node("172.18.0.5", 6379)]  # node "lạ"
            created.append(self)

        async def initialize(self) -> None:
            return None

        def get_nodes(self) -> list[_Node]:
            return self.nodes

    monkeypatch.setattr(mod, "RedisCluster", FakeCluster)
    cfg = {
        "mode": "cluster",
        "nodes": ["r1.corp.vn:7000"],
        "key_prefixes": ["app:"],
        "username": "bot",
    }
    conn = ConnectionContext("r", "Cluster", "redis", cfg, {"password": "pw"}, network=_policy())
    plugin = RedisConnector()
    assert [(e.host, e.port) for e in plugin.network_targets(cfg)] == [("r1.corp.vn", 7000)]

    health = await plugin.test_connection(conn)
    assert not health.healthy and "nội bộ của ARIES" in health.detail
    assert created[0].closed  # đóng kết nối khi bị chặn
    assert created[0].startup[0].host == "r1.corp.vn" and created[0].startup[0].port == 7000
    assert created[0].kw["username"] == "bot" and created[0].kw["password"] == "pw"


async def test_redis_sentinel_uses_discovered_master(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiagent_plugins.connector_redis as mod

    server = FakeServer()
    seen: dict[str, Any] = {}

    class FakeSentinel:
        def __init__(
            self, sentinels: list[Any], sentinel_kwargs: dict[str, Any], **kw: Any
        ) -> None:
            seen.update(points=sentinels, skw=sentinel_kwargs, kw=kw)
            self.sentinels = [_Closable() for _ in sentinels]
            seen["sentinel_clients"] = self.sentinels

        async def discover_master(self, name: str) -> tuple[str, int]:
            seen["service"] = name
            return seen.get("master", ("r1.corp.vn", 6380))

        def master_for(self, name: str, db: int = 0) -> Any:
            return FakeRedis(server=server)

    monkeypatch.setattr(mod, "Sentinel", FakeSentinel)
    cfg = {
        "mode": "sentinel",
        "sentinels": ["s1.corp.vn"],
        "service_name": "mymaster",
        "key_prefixes": ["app:"],
        "allow_write": True,
    }
    secrets = {"password": "pw", "sentinel_password": "spw"}
    conn = ConnectionContext("r", "Sentinel", "redis", cfg, secrets, network=_policy())
    plugin = RedisConnector()
    assert plugin.network_targets(cfg)[0].port == 26379

    r = await plugin.call_tool("redis.set", {"key": "app:x", "value": "1"}, ctx(conn))
    assert not r.is_error, r.content
    got = json.loads((await plugin.call_tool("redis.get", {"key": "app:x"}, ctx(conn))).content)
    assert got["value"] == "1"
    assert seen["points"] == [("s1.corp.vn", 26379)] and seen["service"] == "mymaster"
    assert seen["skw"]["password"] == "spw" and seen["kw"]["password"] == "pw"
    assert all(s.closed for s in seen["sentinel_clients"])

    seen["master"] = ("redis", 6379)  # sentinel bị đầu độc trỏ master vào Redis của ARIES
    r = await plugin.call_tool("redis.get", {"key": "app:x"}, ctx(conn))
    assert r.is_error and "nội bộ của ARIES" in r.content


async def test_grpc_streaming(grpc_target) -> None:  # type: ignore[no-untyped-def]
    target, _ = grpc_target
    plugin = GrpcConnector()
    conn = gconn(target, allowed_services=["shop.v1.OrderService"], stream_timeout_s=1)
    tools = {t.name: t for t in await plugin.discover_tools(conn)}
    for t in tools.values():
        Draft202012Validator.check_schema(t.input_schema)

    watch = tools["grpc.order_service_watch"]
    assert watch.risk is RiskLevel.READ and "server stream" in watch.description
    r = await plugin.call_tool(watch.name, {"id": 10}, ctx(conn, watch))
    data = json.loads(r.content)
    assert data["ended"] == "completed" and [m["id"] for m in data["messages"]] == [
        "10",
        "11",
        "12",
    ]

    # Stream không tự kết thúc → dừng sau stream_timeout_s, trả phần đã nhận
    started = time.monotonic()
    r = await plugin.call_tool(watch.name, {"id": 999}, ctx(conn, watch))
    data = json.loads(r.content)
    assert data["ended"] == "timeout" and data["count"] == 3 and time.monotonic() - started < 5

    small = gconn(target, allowed_services=["shop.v1.OrderService"], max_stream_messages=2)
    data = json.loads((await plugin.call_tool(watch.name, {"id": 1}, ctx(small, watch))).content)
    assert data["ended"] == "limit" and data["count"] == 2

    count = tools["grpc.order_service_count_orders"]
    assert count.input_schema["required"] == ["requests"]
    r = await plugin.call_tool(
        count.name, {"requests": [{"id": 1}, {"id": 2}, {"id": 3}]}, ctx(conn, count)
    )
    assert json.loads(r.content)["id"] == "3"

    bulk = tools["grpc.order_service_bulk_get"]
    assert "bidi" in bulk.description
    r = await plugin.call_tool(bulk.name, {"requests": [{"id": 5}, {"id": 6}]}, ctx(conn, bulk))
    assert [m["sku"] for m in json.loads(r.content)["messages"]] == ["B5", "B6"]
    r = await plugin.call_tool(bulk.name, {"requests": []}, ctx(conn, bulk))
    assert r.is_error and "requests" in r.content
