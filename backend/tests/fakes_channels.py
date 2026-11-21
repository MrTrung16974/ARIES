from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

from aiagent.modules.channels.domain import ApiKey, ShareLink
from aiagent.shared_kernel.clock import utcnow


class InMemoryChannelRepository:
    def __init__(self) -> None:
        self.keys: dict[UUID, ApiKey] = {}
        self.shares: dict[UUID, ShareLink] = {}

    async def create_api_key(self, key: ApiKey) -> ApiKey:
        self.keys[key.id] = key
        return key

    async def list_api_keys(self, tenant_id: str, agent_id: UUID) -> list[ApiKey]:
        return [k for k in self.keys.values() if k.agent_id == agent_id]

    async def get_api_key(self, tenant_id: str, key_id: UUID) -> ApiKey | None:
        k = self.keys.get(key_id)
        return k if k and k.tenant_id == tenant_id else None

    async def revoke_api_key(self, tenant_id: str, key_id: UUID) -> None:
        self.keys[key_id] = replace(self.keys[key_id], revoked_at=utcnow())

    async def touch_api_key(self, tenant_id: str, key_id: UUID) -> None:
        self.keys[key_id] = replace(self.keys[key_id], last_used_at=utcnow())

    async def create_share(self, link: ShareLink) -> ShareLink:
        self.shares[link.id] = link
        return link

    async def list_shares(self, tenant_id: str, agent_id: UUID) -> list[ShareLink]:
        return [s for s in self.shares.values() if s.agent_id == agent_id]

    async def get_share(self, tenant_id: str, share_id: UUID) -> ShareLink | None:
        s = self.shares.get(share_id)
        return s if s and s.tenant_id == tenant_id else None

    async def find_share(self, tenant_id: str, token_hash: str) -> ShareLink | None:
        return next(
            (
                s
                for s in self.shares.values()
                if s.tenant_id == tenant_id and s.token_hash == token_hash
            ),
            None,
        )

    async def save_share(self, link: ShareLink) -> ShareLink:
        self.shares[link.id] = link
        return link

    async def delete_share(self, tenant_id: str, share_id: UUID) -> None:
        self.shares.pop(share_id, None)


# ---------------------------------------------------------------- Telegram
class InMemoryTelegramBotRepository:
    def __init__(self) -> None:
        self.bots: dict[UUID, Any] = {}

    async def create(self, bot: Any) -> Any:
        self.bots[bot.id] = bot
        return bot

    async def list_for_agent(self, tenant_id: str, agent_id: UUID) -> list[Any]:
        return [b for b in self.bots.values() if b.agent_id == agent_id]

    async def get(self, tenant_id: str, bot_id: UUID) -> Any:
        b = self.bots.get(bot_id)
        return b if b and b.tenant_id == tenant_id else None

    async def save(self, bot: Any) -> Any:
        cur = self.bots[bot.id]
        self.bots[bot.id] = replace(
            cur,
            name=bot.name,
            enabled=bot.enabled,
            allowed_chat_ids=bot.allowed_chat_ids,
            rate_limit_per_min=bot.rate_limit_per_min,
        )
        return self.bots[bot.id]

    async def touch(self, tenant_id: str, bot_id: UUID) -> None:
        self.bots[bot_id] = replace(self.bots[bot_id], last_used_at=utcnow())

    async def set_offset(self, tenant_id: str, bot_id: UUID, offset: int) -> None:
        cur = self.bots[bot_id]
        self.bots[bot_id] = replace(cur, update_offset=max(cur.update_offset, offset))

    async def delete(self, tenant_id: str, bot_id: UUID) -> None:
        self.bots.pop(bot_id, None)


class FakeTelegramClient:
    """Bot API giả: token hợp lệ khi chứa 'GOOD'; ghi lại tin đã gửi, update chờ lấy."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.webhooks: dict[str, tuple[str, str] | None] = {}
        self.pending: list[dict[str, Any]] = []
        self.offsets: list[int] = []

    async def get_me(self, token: str) -> Any:
        from aiagent.modules.channels.domain.telegram import BotIdentity, TelegramError

        if "GOOD" not in token:
            raise TelegramError("getMe: Unauthorized")
        return BotIdentity(id=int(token.split(":")[0]), username="aries_test_bot")

    async def set_webhook(self, token: str, url: str, secret: str) -> None:
        self.webhooks[token] = (url, secret)

    async def delete_webhook(self, token: str) -> None:
        self.webhooks[token] = None

    async def get_updates(self, token: str, offset: int, wait_s: int) -> list[dict[str, Any]]:
        self.offsets.append(offset)
        out = [u for u in self.pending if u["update_id"] >= offset]
        return out

    async def send_message(self, token: str, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))

    async def send_typing(self, token: str, chat_id: int) -> None:
        return None


class MemoryPollRegistry:
    def __init__(self) -> None:
        self.items: set[tuple[str, UUID]] = set()

    async def add(self, tenant_id: str, bot_id: UUID) -> None:
        self.items.add((tenant_id, bot_id))

    async def remove(self, tenant_id: str, bot_id: UUID) -> None:
        self.items.discard((tenant_id, bot_id))

    async def members(self) -> list[tuple[str, UUID]]:
        return sorted(self.items)
