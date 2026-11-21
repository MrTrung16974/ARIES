"""Adapter Telegram: Bot API qua egress proxy, bảng channels.telegram_bots, registry polling."""

from __future__ import annotations

import contextlib
import json
from typing import Any
from uuid import UUID

import httpx
from redis.asyncio import Redis
from sqlalchemy import text

from aiagent.adapters.db import Database
from aiagent.modules.channels.domain.telegram import (
    AgentAnswer,
    BotIdentity,
    TelegramBot,
    TelegramError,
)
from aiagent.modules.identity.domain import Principal
from aiagent.modules.runtime.application import RunAgentService
from aiagent.shared_kernel.errors import ConflictError, NotFoundError


class HttpTelegramClient:
    """Bot API. Token nằm trong URL → KHÔNG để lộ URL/ngoại lệ httpx ra ngoài."""

    def __init__(self, http: httpx.AsyncClient, base_url: str = "https://api.telegram.org") -> None:
        self._http = http
        self._base = base_url.rstrip("/")

    async def _call(
        self, token: str, method: str, payload: dict[str, Any], wait_s: float = 15
    ) -> Any:
        try:
            r = await self._http.post(
                f"{self._base}/bot{token}/{method}", json=payload, timeout=wait_s
            )
        except httpx.HTTPError as exc:
            raise TelegramError(
                f"{method}: không kết nối được Telegram ({type(exc).__name__})"
            ) from None
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(f"{method}: HTTP {r.status_code}") from None
        if not isinstance(body, dict) or not body.get("ok"):
            desc = str(body.get("description", "")) if isinstance(body, dict) else ""
            raise TelegramError(f"{method}: {desc[:200] or f'HTTP {r.status_code}'}")
        return body.get("result")

    async def get_me(self, token: str) -> BotIdentity:
        res = await self._call(token, "getMe", {})
        return BotIdentity(id=int(res["id"]), username=str(res.get("username", "")))

    async def set_webhook(self, token: str, url: str, secret: str) -> None:
        await self._call(
            token,
            "setWebhook",
            {
                "url": url,
                "secret_token": secret,
                "allowed_updates": ["message"],
                "drop_pending_updates": True,
            },
        )

    async def delete_webhook(self, token: str) -> None:
        await self._call(token, "deleteWebhook", {"drop_pending_updates": False})

    async def get_updates(self, token: str, offset: int, wait_s: int) -> list[dict[str, Any]]:
        res = await self._call(
            token,
            "getUpdates",
            {"offset": offset, "timeout": wait_s, "allowed_updates": ["message"]},
            wait_s=wait_s + 10,
        )
        return [u for u in res or [] if isinstance(u, dict)]

    async def send_message(self, token: str, chat_id: int, text: str) -> None:
        # Văn bản thuần (không parse_mode) → không lỗi vì Markdown sai, không chèn được link ẩn.
        await self._call(
            token,
            "sendMessage",
            {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}},
        )

    async def send_typing(self, token: str, chat_id: int) -> None:
        with contextlib.suppress(TelegramError):  # chỉ là hiệu ứng "đang gõ"
            await self._call(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})


_SELECT = """SELECT id, tenant_id, agent_id, name, bot_user_id, bot_username, mode,
           webhook_secret_hash,
           enabled, allowed_chat_ids, rate_limit_per_min, update_offset, created_by, created_at,
           last_used_at FROM channels.telegram_bots """
_INSERT = text(
    """
    INSERT INTO channels.telegram_bots
      (id, tenant_id, agent_id, name, bot_user_id, bot_username, mode, webhook_secret_hash,
       enabled, allowed_chat_ids, rate_limit_per_min, created_by)
    VALUES (:id, :tenant_id, :agent_id, :name, :bot_user_id, :bot_username, :mode, :secret_hash,
            :enabled, CAST(:allowed AS jsonb), :rate, :created_by)
    """
)
_LIST = text(_SELECT + "WHERE agent_id = :agent_id ORDER BY created_at DESC")
_GET = text(_SELECT + "WHERE id = :id")
_SAVE = text(
    """
    UPDATE channels.telegram_bots
       SET name = :name, enabled = :enabled, allowed_chat_ids = CAST(:allowed AS jsonb),
           rate_limit_per_min = :rate
     WHERE id = :id
    """
)
_TOUCH = text("UPDATE channels.telegram_bots SET last_used_at = now() WHERE id = :id")
_OFFSET = text(
    "UPDATE channels.telegram_bots SET update_offset = GREATEST(update_offset, :offset) "
    "WHERE id = :id"
)
_DELETE = text("DELETE FROM channels.telegram_bots WHERE id = :id")


def _bot(m: Any) -> TelegramBot:
    return TelegramBot(
        id=m["id"],
        tenant_id=m["tenant_id"],
        agent_id=m["agent_id"],
        name=m["name"],
        bot_user_id=int(m["bot_user_id"]),
        bot_username=m["bot_username"],
        mode=m["mode"],
        webhook_secret_hash=m["webhook_secret_hash"],
        enabled=bool(m["enabled"]),
        allowed_chat_ids=[int(x) for x in m["allowed_chat_ids"] or []],
        rate_limit_per_min=int(m["rate_limit_per_min"]),
        update_offset=int(m["update_offset"]),
        created_by=m["created_by"],
        created_at=m["created_at"],
        last_used_at=m["last_used_at"],
    )


class SqlTelegramBotRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, bot: TelegramBot) -> TelegramBot:
        async with self._db.tenant_session(bot.tenant_id) as s:
            await s.execute(
                _INSERT,
                {
                    "id": bot.id,
                    "tenant_id": bot.tenant_id,
                    "agent_id": bot.agent_id,
                    "name": bot.name,
                    "bot_user_id": bot.bot_user_id,
                    "bot_username": bot.bot_username,
                    "mode": bot.mode,
                    "secret_hash": bot.webhook_secret_hash,
                    "enabled": bot.enabled,
                    "allowed": json.dumps(bot.allowed_chat_ids),
                    "rate": bot.rate_limit_per_min,
                    "created_by": bot.created_by,
                },
            )
        found = await self.get(bot.tenant_id, bot.id)
        if found is None:
            raise NotFoundError()
        return found

    async def list_for_agent(self, tenant_id: str, agent_id: UUID) -> list[TelegramBot]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST, {"agent_id": agent_id})).all()
        return [_bot(r._mapping) for r in rows]

    async def get(self, tenant_id: str, bot_id: UUID) -> TelegramBot | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET, {"id": bot_id})).first()
        return _bot(row._mapping) if row else None

    async def save(self, bot: TelegramBot) -> TelegramBot:
        async with self._db.tenant_session(bot.tenant_id) as s:
            await s.execute(
                _SAVE,
                {
                    "id": bot.id,
                    "name": bot.name,
                    "enabled": bot.enabled,
                    "allowed": json.dumps(bot.allowed_chat_ids),
                    "rate": bot.rate_limit_per_min,
                },
            )
        found = await self.get(bot.tenant_id, bot.id)
        if found is None:
            raise NotFoundError()
        return found

    async def touch(self, tenant_id: str, bot_id: UUID) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(_TOUCH, {"id": bot_id})

    async def set_offset(self, tenant_id: str, bot_id: UUID, offset: int) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(_OFFSET, {"id": bot_id, "offset": offset})

    async def delete(self, tenant_id: str, bot_id: UUID) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(_DELETE, {"id": bot_id})


class RedisPollRegistry:
    _KEY = "aries:telegram:polling"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def add(self, tenant_id: str, bot_id: UUID) -> None:
        await self._redis.sadd(self._KEY, f"{tenant_id}|{bot_id}")  # type: ignore[misc]

    async def remove(self, tenant_id: str, bot_id: UUID) -> None:
        await self._redis.srem(self._KEY, f"{tenant_id}|{bot_id}")  # type: ignore[misc]

    async def members(self) -> list[tuple[str, UUID]]:
        raw = await self._redis.smembers(self._KEY)  # type: ignore[misc]
        out: list[tuple[str, UUID]] = []
        for item in raw:
            value = item.decode() if isinstance(item, bytes) else str(item)
            tenant, _, bot = value.partition("|")
            try:
                out.append((tenant, UUID(bot)))
            except ValueError:
                continue
        return sorted(out)


class RuntimeAgentRunner:
    """Chạy agent hết một lượt và gom câu trả lời cuối (như API key không stream)."""

    def __init__(self, runs: RunAgentService) -> None:
        self._runs = runs

    async def ask(
        self, principal: Principal, agent_id: UUID, message: str, thread_id: UUID | None
    ) -> AgentAnswer:
        try:
            done = await self._runs.run_once(principal, agent_id, message, thread_id=thread_id)
        except (ConflictError, NotFoundError):
            if thread_id is None:
                raise
            # Agent đã publish version mới → mở hội thoại mới cho chat này.
            done = await self._runs.run_once(principal, agent_id, message, thread_id=None)
        return AgentAnswer(
            thread_id=done.thread_id, status=done.status, text=done.text, citations=done.citations
        )
