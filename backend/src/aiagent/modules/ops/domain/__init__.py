"""Vận hành (ADR-0020): nhật ký hội thoại mọi kênh, tổng hợp feedback, chi phí/usage.

Read model thuần đọc trên schema `conversations`; kênh suy ra từ tiền tố `user_id`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol
from uuid import UUID

CHANNELS = ("app", "share", "api", "telegram")

# Tiền tố subject của principal kênh → tên kênh (identity: Principal.channel).
_PREFIXES = (("share:", "share"), ("apikey:", "api"), ("telegram:", "telegram"))


def channel_of(user_id: str) -> str:
    for prefix, name in _PREFIXES:
        if user_id.startswith(prefix):
            return name
    return "app"


@dataclass(frozen=True)
class OpsFilter:
    agent_id: UUID | None = None
    channel: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    rating: int | None = None  # 1 | -1: chỉ hội thoại/feedback có đánh giá này


@dataclass(frozen=True)
class ThreadSummary:
    id: UUID
    agent_id: UUID
    user_id: str
    channel: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    cost_usd: float
    thumbs_up: int
    thumbs_down: int


@dataclass(frozen=True)
class FeedbackItem:
    id: UUID
    thread_id: UUID
    message_id: int
    agent_id: UUID
    user_id: str
    channel: str
    rating: int
    comment: str
    created_at: datetime
    answer: list[dict[str, Any]]  # content block gốc của câu trả lời được đánh giá


@dataclass(frozen=True)
class UsageRow:
    day: date
    agent_id: UUID
    channel: str
    runs: int
    failed_runs: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class OpsQueryPort(Protocol):
    async def threads(
        self, tenant_id: str, f: OpsFilter, limit: int, before: datetime | None
    ) -> list[ThreadSummary]: ...

    async def feedback(self, tenant_id: str, f: OpsFilter, limit: int) -> list[FeedbackItem]: ...

    async def usage(self, tenant_id: str, f: OpsFilter) -> list[UsageRow]: ...


def text_of(content: list[dict[str, Any]], limit: int = 500) -> str:
    """Chỉ phần text của câu trả lời (bỏ thinking/tool) — dùng cho bảng feedback."""
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    return text if len(text) <= limit else text[: limit - 1] + "…"
