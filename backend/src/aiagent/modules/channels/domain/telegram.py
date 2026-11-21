"""Kênh Telegram (ADR-0020 §5).

Bot token là bí mật → chỉ nằm ở OpenBao (`secret_path`), không bao giờ vào DB/log/API.
Mỗi chat riêng (private) = một hội thoại; subject = `telegram:<bot>:<chat_id>`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")
MAX_MESSAGE = 4096  # giới hạn độ dài tin nhắn Telegram


def secret_path(tenant_id: str, bot_id: UUID) -> str:
    return f"aries/{tenant_id}/channels/telegram/{bot_id}"


@dataclass(frozen=True)
class TelegramBot:
    id: UUID
    tenant_id: str
    agent_id: UUID
    name: str
    bot_user_id: int
    bot_username: str
    mode: str  # "webhook" | "polling"
    webhook_secret_hash: str = field(repr=False)
    enabled: bool
    allowed_chat_ids: list[int]  # rỗng = mọi người (chỉ cho agent public)
    rate_limit_per_min: int
    update_offset: int
    created_by: str
    created_at: datetime
    last_used_at: datetime | None


@dataclass(frozen=True)
class BotIdentity:
    id: int
    username: str


class TelegramError(Exception):
    """Lỗi gọi Bot API — thông điệp KHÔNG chứa URL (URL có token)."""


class TelegramClientPort(Protocol):
    async def get_me(self, token: str) -> BotIdentity: ...

    async def set_webhook(self, token: str, url: str, secret: str) -> None: ...

    async def delete_webhook(self, token: str) -> None: ...

    async def get_updates(self, token: str, offset: int, wait_s: int) -> list[dict[str, Any]]: ...

    async def send_message(self, token: str, chat_id: int, text: str) -> None: ...

    async def send_typing(self, token: str, chat_id: int) -> None: ...


class TelegramBotRepositoryPort(Protocol):
    async def create(self, bot: TelegramBot) -> TelegramBot: ...

    async def list_for_agent(self, tenant_id: str, agent_id: UUID) -> list[TelegramBot]: ...

    async def get(self, tenant_id: str, bot_id: UUID) -> TelegramBot | None: ...

    async def save(self, bot: TelegramBot) -> TelegramBot:
        """Cập nhật name/enabled/allowed_chat_ids/rate_limit_per_min."""
        ...

    async def touch(self, tenant_id: str, bot_id: UUID) -> None: ...

    async def set_offset(self, tenant_id: str, bot_id: UUID, offset: int) -> None: ...

    async def delete(self, tenant_id: str, bot_id: UUID) -> None: ...


class PollRegistryPort(Protocol):
    """Danh sách bot chạy long polling (worker không duyệt được mọi tenant qua RLS)."""

    async def add(self, tenant_id: str, bot_id: UUID) -> None: ...

    async def remove(self, tenant_id: str, bot_id: UUID) -> None: ...

    async def members(self) -> list[tuple[str, UUID]]: ...


@dataclass(frozen=True)
class AgentAnswer:
    thread_id: UUID
    status: str
    text: str
    citations: list[dict[str, Any]]


class AgentRunnerPort(Protocol):
    """Chạy agent đến hết và trả câu trả lời cuối (dùng cho kênh không stream được)."""

    async def ask(
        self, principal: Any, agent_id: UUID, message: str, thread_id: UUID | None
    ) -> AgentAnswer: ...


def split_message(text: str, limit: int = MAX_MESSAGE) -> list[str]:
    """Chia câu trả lời dài thành nhiều tin ≤ limit, ưu tiên cắt ở xuống dòng."""
    text = text.strip()
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts
