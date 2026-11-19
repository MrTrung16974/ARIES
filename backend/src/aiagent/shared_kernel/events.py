"""Domain event và event bus.

Giai đoạn đầu dùng bus in-process; sau này thay bằng Redis Streams/Kafka qua cùng `EventBusPort`
(xem docs/architecture.md mục 8).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid4

from aiagent.shared_kernel.clock import utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=utcnow)
    tenant_id: str | None = None

    @property
    def name(self) -> str:
        return type(self).__name__


EventHandler = Callable[[DomainEvent], Awaitable[None]]


class EventBusPort(Protocol):
    def subscribe(self, event_type: type[DomainEvent], handler: EventHandler) -> None: ...

    async def publish(self, event: DomainEvent) -> None: ...


class InProcessEventBus:
    """Bus đơn giản trong tiến trình. Lỗi của một handler không chặn các handler khác."""

    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: type[DomainEvent], handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        for event_type, handlers in self._handlers.items():
            if not isinstance(event, event_type):
                continue
            for handler in handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.exception("Event handler failed", extra={"event": event.name})
