"""Connector Kafka (ADR-0023).

- `kafka.list_topics`: các topic được phép + số partition.
- `kafka.peek`: đọc N message MỚI NHẤT của một topic — gán partition trực tiếp, không consumer group,
  không commit offset (không ảnh hưởng consumer khác). Nội dung là dữ liệu không tin cậy.
- `kafka.produce`: gửi một message (external_send → quét lộ secret + cần duyệt); chỉ khi
  `allow_produce`.
- Phạm vi theo `allowed_topics` (tên chính xác hoặc tiền tố kết thúc bằng `*`).
- Giới hạn: core chỉ kiểm `bootstrap_servers`; broker do cluster quảng bá (advertised listeners)
  không được kiểm lại — đặt `connector_allowed_networks` nếu cần chặn chặt.
"""

from __future__ import annotations

import asyncio
import heapq
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

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
    ToolNotFoundError,
    ToolResult,
    ToolSpec,
    TriggerEvent,
    TriggerSpec,
)
from aiagent.plugin_sdk.net import parse_host_port
from aiagent.plugin_sdk.tls import CLIENT_KEY_SECRET, TlsConfigError, ssl_context, tls_properties

MAX_VALUE_CHARS = 2000
MAX_TRIGGER_EVENTS = 10  # sự kiện (lần chạy agent) mỗi lần kiểm tra
MAX_TRIGGER_SCAN = 500  # message quét mỗi lần kiểm tra (kể cả không khớp bộ lọc)
MAX_EVENT_CHARS = 15_000
MAX_TOPICS = 500
PROTOCOLS = ["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"]
MECHANISMS = ["PLAIN", "SCRAM-SHA-256", "SCRAM-SHA-512"]

_TOPIC = {"type": "string", "pattern": r"^[A-Za-z0-9._\-]{1,249}$"}

_CONFIG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bootstrap_servers": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 260},
            "minItems": 1,
            "maxItems": 20,
            "title": "Bootstrap servers",
            "description": "host:port, ví dụ kafka1.congty.vn:9092",
        },
        "security_protocol": {
            "type": "string",
            "enum": PROTOCOLS,
            "default": "PLAINTEXT",
            "title": "Giao thức bảo mật",
        },
        "sasl_mechanism": {"type": "string", "enum": MECHANISMS, "default": "PLAIN"},
        "username": {"type": "string", "maxLength": 200, "title": "Người dùng SASL"},
        "allowed_topics": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^[A-Za-z0-9._\-]{1,249}\*?$"},
            "minItems": 1,
            "maxItems": 100,
            "title": "Topic được phép",
            "description": "Tên chính xác hoặc tiền tố kết thúc bằng *, ví dụ orders.*",
        },
        "max_messages": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
        "timeout_s": {"type": "integer", "minimum": 1, "maximum": 60, "default": 10},
        **tls_properties(),
        "allow_produce": {
            "type": "boolean",
            "default": False,
            "title": "Cho phép gửi message (cần duyệt)",
        },
    },
    "required": ["bootstrap_servers", "allowed_topics"],
    "additionalProperties": False,
}

_MANIFEST = PluginManifest(
    id="kafka",
    kind=PluginKind.CONNECTOR,
    name="Kafka",
    description="Xem topic và đọc message mới nhất trên Kafka; gửi message khi được bật (cần duyệt).",
    version="1.0.0",
    api_version=PLUGIN_API_VERSION,
    config_schema=_CONFIG_SCHEMA,
    secrets=("password", CLIENT_KEY_SECRET),
    optional_secrets=("password", CLIENT_KEY_SECRET),
    tools=(
        ToolSpec(
            name="kafka.list_topics",
            description="Liệt kê các topic được phép truy cập và số partition.",
            risk=RiskLevel.READ,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ToolSpec(
            name="kafka.peek",
            description=(
                "Đọc các message mới nhất của một topic (không commit offset, không ảnh hưởng "
                "consumer khác). Có thể chỉ định partition."
            ),
            risk=RiskLevel.READ,
            untrusted_output=True,
            input_schema={
                "type": "object",
                "properties": {
                    "topic": _TOPIC,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "partition": {"type": "integer", "minimum": 0, "maximum": 10000},
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="kafka.produce",
            description=(
                "Gửi một message (value dạng chuỗi, thường là JSON) vào topic. Chỉ dùng khi người "
                "dùng yêu cầu rõ ràng; cần duyệt."
            ),
            risk=RiskLevel.EXTERNAL_SEND,
            input_schema={
                "type": "object",
                "properties": {
                    "topic": _TOPIC,
                    "value": {"type": "string", "maxLength": 100_000},
                    "key": {"type": "string", "maxLength": 1000},
                },
                "required": ["topic", "value"],
                "additionalProperties": False,
            },
        ),
    ),
    triggers=(
        TriggerSpec(
            name="kafka.new_message",
            description="Có message mới trên một topic được phép.",
            config_schema={
                "type": "object",
                "properties": {
                    "topic": {**_TOPIC, "title": "Topic"},
                    "contains": {
                        "type": "string",
                        "maxLength": 100,
                        "title": "Chỉ khi nội dung chứa (tùy chọn)",
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        ),
    ),
    ui_hints={"icon": "hub", "category": "data", "multiline_secrets": [CLIENT_KEY_SECRET]},
)


@dataclass(frozen=True)
class KafkaMessage:
    partition: int
    offset: int
    timestamp: int  # ms epoch
    key: bytes | None
    value: bytes | None


class KafkaGateway(Protocol):
    """Lớp mỏng quanh client Kafka (thay bằng bản giả trong test)."""

    async def topics(self, conn: ConnectionContext) -> dict[str, int]:
        """Topic → số partition."""
        ...

    async def latest(
        self, conn: ConnectionContext, topic: str, partitions: list[int], limit: int
    ) -> list[KafkaMessage]: ...

    async def produce(
        self, conn: ConnectionContext, topic: str, key: bytes | None, value: bytes
    ) -> tuple[int, int]:
        """(partition, offset)."""
        ...

    async def read_from(
        self,
        conn: ConnectionContext,
        topic: str,
        starts: dict[int, int] | None,
        max_records: int,
    ) -> tuple[list[KafkaMessage], dict[int, int]]:
        """Message từ `starts` (offset theo partition; partition mới đọc từ đầu) tới cuối hiện
        tại, tối đa `max_records`; `starts=None` chỉ lấy offset cuối. Trả (message, offset cuối)."""
        ...


class TopicRejectedError(ValueError):
    pass


def topic_allowed(topic: str, allowed: list[str]) -> bool:
    for rule in allowed:
        if rule.endswith("*"):
            if topic.startswith(rule[:-1]):
                return True
        elif topic == rule:
            return True
    return False


def client_options(conn: ConnectionContext) -> dict[str, Any]:
    """Tham số chung cho AIOKafka* từ cấu hình connection."""
    cfg = conn.config
    protocol = str(cfg.get("security_protocol", "PLAINTEXT"))
    timeout_ms = int(cfg.get("timeout_s", 10)) * 1000
    opts: dict[str, Any] = {
        "bootstrap_servers": list(cfg["bootstrap_servers"]),
        "security_protocol": protocol,
        "client_id": "aries-agent",
        "request_timeout_ms": max(timeout_ms, 5000),
        "metadata_max_age_ms": 60_000,
    }
    if protocol.startswith("SASL"):
        opts["sasl_mechanism"] = str(cfg.get("sasl_mechanism", "PLAIN"))
        opts["sasl_plain_username"] = str(cfg.get("username", ""))
        opts["sasl_plain_password"] = conn.secrets.get("password", "")
    if protocol.endswith("SSL"):
        opts["ssl_context"] = ssl_context(cfg, conn.secrets)  # CA riêng + mTLS nếu có
    return opts


class AioKafkaGateway:
    async def topics(self, conn: ConnectionContext) -> dict[str, int]:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(**client_options(conn), enable_auto_commit=False)
        await consumer.start()
        try:
            names = await consumer.topics()
            return {
                t: len(consumer.partitions_for_topic(t) or ())
                for t in sorted(names)[: MAX_TOPICS * 4]
            }
        finally:
            await consumer.stop()

    async def _read(
        self,
        conn: ConnectionContext,
        topic: str,
        partitions: list[int],
        start_of: Callable[[int, int, int], int],
        max_records: int,
    ) -> tuple[list[KafkaMessage], dict[int, int]]:
        """Đọc từ offset do `start_of(partition, đầu, cuối)` chọn tới cuối hiện tại.

        Không consumer group, không commit. Trả (message, offset cuối theo partition).
        """
        from aiokafka import AIOKafkaConsumer, TopicPartition

        consumer = AIOKafkaConsumer(**client_options(conn), enable_auto_commit=False, group_id=None)
        await consumer.start()
        try:
            await consumer.topics()  # nạp metadata
            known = consumer.partitions_for_topic(topic) or set()
            chosen = [p for p in partitions if p in known] if partitions else sorted(known)
            tps = [TopicPartition(topic, p) for p in chosen]
            if not tps:
                return [], {}
            consumer.assign(tps)
            ends = await consumer.end_offsets(tps)
            begins = await consumer.beginning_offsets(tps)
            pending = {}
            for tp in tps:
                start = max(begins[tp], start_of(tp.partition, begins[tp], ends[tp]))
                if start < ends[tp]:
                    consumer.seek(tp, start)
                    pending[tp] = ends[tp]
            out: list[KafkaMessage] = []
            loop = asyncio.get_running_loop()
            deadline = loop.time() + float(conn.config.get("timeout_s", 10))
            while pending and len(out) < max_records and loop.time() < deadline:
                batch = await consumer.getmany(timeout_ms=500, max_records=max_records)
                for tp, records in batch.items():
                    for rec in records:
                        if tp in pending and rec.offset < pending[tp]:
                            out.append(
                                KafkaMessage(
                                    rec.partition, rec.offset, rec.timestamp, rec.key, rec.value
                                )
                            )
                    if tp in pending and records and records[-1].offset >= pending[tp] - 1:
                        del pending[tp]
            return out, {tp.partition: ends[tp] for tp in tps}
        finally:
            await consumer.stop()

    async def latest(
        self, conn: ConnectionContext, topic: str, partitions: list[int], limit: int
    ) -> list[KafkaMessage]:
        messages, _ = await self._read(
            conn, topic, partitions, lambda p, begin, end: end - limit, limit * 64
        )
        return messages

    async def read_from(
        self,
        conn: ConnectionContext,
        topic: str,
        starts: dict[int, int] | None,
        max_records: int,
    ) -> tuple[list[KafkaMessage], dict[int, int]]:
        if starts is None:  # chỉ lấy offset cuối (lần đầu của trigger)
            return await self._read(conn, topic, [], lambda p, begin, end: end, 0)
        return await self._read(
            conn, topic, [], lambda p, begin, end: starts.get(p, begin), max_records
        )

    async def produce(
        self, conn: ConnectionContext, topic: str, key: bytes | None, value: bytes
    ) -> tuple[int, int]:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(**client_options(conn), acks="all")
        await producer.start()
        try:
            meta = await producer.send_and_wait(topic, value=value, key=key)
            return meta.partition, meta.offset
        finally:
            await producer.stop()


def _text(raw: bytes | None) -> Any:
    if raw is None:
        return None
    text = raw.decode("utf-8", "replace")
    try:
        value = json.loads(text)
        if isinstance(value, dict | list) and len(text) <= MAX_VALUE_CHARS:
            return value
    except ValueError:
        pass
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS] + "…"


def _merge_in_order(messages: list[KafkaMessage]) -> Iterator[KafkaMessage]:
    """Giữ đúng thứ tự offset trong từng partition (timestamp do producer đặt có thể lộn xộn);
    giữa các partition, lấy message cũ nhất ở đầu hàng trước (trộn k chiều)."""
    queues: dict[int, list[KafkaMessage]] = {}
    for m in messages:
        queues.setdefault(m.partition, []).append(m)
    for q in queues.values():
        q.sort(key=lambda m: m.offset, reverse=True)  # pop() lấy offset nhỏ nhất
    heap = [(q[-1].timestamp, p) for p, q in queues.items()]
    heapq.heapify(heap)
    while heap:
        _, p = heapq.heappop(heap)
        q = queues[p]
        yield q.pop()
        if q:
            heapq.heappush(heap, (q[-1].timestamp, p))


def _event_text(m: KafkaMessage) -> str:
    key = m.key.decode("utf-8", "replace") if m.key else ""
    value = m.value.decode("utf-8", "replace") if m.value else ""
    text = (f"key: {key}\n" if key else "") + f"value: {value}"
    return text if len(text) <= MAX_EVENT_CHARS else text[:MAX_EVENT_CHARS] + "…"


class KafkaConnector(Plugin):
    def __init__(self, gateway: KafkaGateway | None = None) -> None:
        self._gw: KafkaGateway = gateway or AioKafkaGateway()

    @property
    def manifest(self) -> PluginManifest:
        return _MANIFEST

    def network_targets(self, config: dict[str, Any]) -> list[Endpoint]:
        return [parse_host_port(str(s), 9092) for s in config.get("bootstrap_servers") or []]

    @staticmethod
    def _check_topic(conn: ConnectionContext, topic: str) -> str:
        if not topic_allowed(topic, list(conn.config.get("allowed_topics") or [])):
            raise TopicRejectedError(f"Topic '{topic[:100]}' không nằm trong danh sách được phép.")
        return topic

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        conn = ctx.connection
        if conn is None:
            return ToolResult("Tool này cần một kết nối Kafka.", is_error=True)
        timeout = float(conn.config.get("timeout_s", 10)) + 5
        try:
            async with asyncio.timeout(timeout):
                if name == "kafka.list_topics":
                    return await self._list(conn)
                if name == "kafka.peek":
                    return await self._peek(conn, arguments)
                if name == "kafka.produce":
                    return await self._produce(conn, arguments)
        except (TopicRejectedError, TlsConfigError) as exc:
            return ToolResult(str(exc), is_error=True)
        except TimeoutError:
            return ToolResult("Kafka không phản hồi kịp (timeout).", is_error=True)
        except Exception as exc:  # aiokafka ném nhiều loại lỗi khác nhau
            return ToolResult(f"Lỗi Kafka: {type(exc).__name__}", is_error=True)
        raise ToolNotFoundError(name)

    async def _list(self, conn: ConnectionContext) -> ToolResult:
        allowed = list(conn.config.get("allowed_topics") or [])
        topics = {
            t: n for t, n in (await self._gw.topics(conn)).items() if topic_allowed(t, allowed)
        }
        items = [{"topic": t, "partitions": n} for t, n in sorted(topics.items())[:MAX_TOPICS]]
        return ToolResult(json.dumps({"topics": items}, ensure_ascii=False))

    async def _peek(self, conn: ConnectionContext, args: dict[str, Any]) -> ToolResult:
        topic = self._check_topic(conn, str(args["topic"]))
        cap = int(conn.config.get("max_messages", 20))
        limit = min(int(args.get("limit") or cap), cap)
        partitions = [int(args["partition"])] if args.get("partition") is not None else []
        messages = await self._gw.latest(conn, topic, partitions, limit)
        messages = sorted(messages, key=lambda m: (m.timestamp, m.offset), reverse=True)[:limit]
        items = [
            {
                "partition": m.partition,
                "offset": m.offset,
                "timestamp_ms": m.timestamp,
                "key": _text(m.key),
                "value": _text(m.value),
            }
            for m in messages
        ]
        payload = {"topic": topic, "count": len(items), "messages": items}
        return ToolResult(json.dumps(payload, ensure_ascii=False), data={"count": len(items)})

    async def _produce(self, conn: ConnectionContext, args: dict[str, Any]) -> ToolResult:
        if not conn.config.get("allow_produce", False):
            return ToolResult(
                "Kết nối này không cho gửi message (allow_produce = false).", is_error=True
            )
        topic = self._check_topic(conn, str(args["topic"]))
        key = str(args["key"]).encode() if args.get("key") else None
        partition, offset = await self._gw.produce(conn, topic, key, str(args["value"]).encode())
        return ToolResult(f"Đã gửi vào {topic} (partition {partition}, offset {offset}).")

    async def poll_trigger(
        self,
        name: str,
        connection: ConnectionContext,
        state: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[list[TriggerEvent], dict[str, Any]]:
        """Message mới trên topic kể từ offset đã lưu ở `state` (core lưu, không commit Kafka).

        Lần đầu (hoặc đổi topic): chỉ ghi mốc offset cuối, không xử lý message cũ. Mỗi lần quét
        tối đa MAX_TRIGGER_SCAN message, phát tối đa MAX_TRIGGER_EVENTS sự kiện khớp bộ lọc;
        message không khớp vẫn được tính là đã đọc.
        """
        if name != "kafka.new_message":
            raise NotImplementedError(name)
        topic = self._check_topic(connection, str(config.get("topic", "")))
        stored = state.get("offsets") if state.get("topic") == topic else None
        if not isinstance(stored, dict):
            _, ends = await self._gw.read_from(connection, topic, None, 0)
            return [], {"topic": topic, "offsets": {str(p): e for p, e in ends.items()}}
        offsets = {int(p): int(o) for p, o in stored.items()}
        messages, ends = await self._gw.read_from(connection, topic, offsets, MAX_TRIGGER_SCAN)
        for p in ends:
            offsets.setdefault(p, 0)  # partition mới: đọc từ đầu ở lần sau nếu chưa có gì
        want = str(config.get("contains") or "").lower()
        events: list[TriggerEvent] = []
        for m in _merge_in_order(messages):
            if m.offset < offsets.get(m.partition, 0):
                continue
            body = _event_text(m)
            if want and want not in body.lower():
                offsets[m.partition] = m.offset + 1
                continue
            if len(events) >= MAX_TRIGGER_EVENTS:
                break  # phần còn lại để lần sau (không bỏ sót)
            events.append(
                TriggerEvent(
                    id=f"kafka:{topic}:{m.partition}:{m.offset}",
                    title=f"{topic} · p{m.partition} #{m.offset}",
                    content=body,
                )
            )
            offsets[m.partition] = m.offset + 1
        return events, {"topic": topic, "offsets": {str(p): o for p, o in offsets.items()}}

    async def test_connection(self, connection: ConnectionContext) -> HealthStatus:
        allowed = list(connection.config.get("allowed_topics") or [])
        try:
            async with asyncio.timeout(float(connection.config.get("timeout_s", 10)) + 5):
                topics = await self._gw.topics(connection)
        except TlsConfigError as exc:
            return HealthStatus(False, str(exc))
        except TimeoutError:
            return HealthStatus(False, "Không kết nối được tới Kafka (timeout).")
        except Exception as exc:
            return HealthStatus(False, f"Không kết nối được tới Kafka ({type(exc).__name__}).")
        visible = [t for t in topics if topic_allowed(t, allowed)]
        note = f"Kết nối OK — thấy {len(visible)}/{len(topics)} topic trong phạm vi."
        return HealthStatus(True, note)
