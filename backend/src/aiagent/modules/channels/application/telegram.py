"""Kênh Telegram (ADR-0020 §5): quản lý bot + xử lý update (webhook hoặc long polling).

Quy tắc an toàn:
- token bot chỉ ở OpenBao; mọi lỗi Bot API được báo không kèm URL (URL chứa token);
- chỉ chat riêng (private) — tin trong nhóm bị bỏ qua;
- agent không `public` bắt buộc có danh sách chat được phép (bot Telegram ai cũng nhắn được);
- principal kênh không có vai trò/người duyệt (APPROVE → DENY ở ToolGateway).
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from aiagent.modules.agents.application import AgentService
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.channels.domain import RateLimiterPort, hash_secret, new_secret
from aiagent.modules.channels.domain.telegram import (
    TOKEN_RE,
    AgentRunnerPort,
    PollRegistryPort,
    TelegramBot,
    TelegramBotRepositoryPort,
    TelegramClientPort,
    TelegramError,
    secret_path,
    split_message,
)
from aiagent.modules.connections.domain import SecretVaultPort
from aiagent.modules.conversations.domain import ConversationRepositoryPort
from aiagent.modules.identity.domain import Principal
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)

logger = logging.getLogger(__name__)

MODES = ("webhook", "polling")
MAX_ALLOWED_CHATS = 200

MSG_TEXT_ONLY = "Hiện trợ lý chỉ hiểu tin nhắn văn bản."
MSG_NOT_ALLOWED = "Bạn chưa được cấp quyền dùng trợ lý này. Mã chat của bạn: {chat_id}"
MSG_RATE = "Bạn gửi hơi nhanh — vui lòng đợi một chút rồi thử lại."
MSG_NEW = "Đã bắt đầu cuộc trò chuyện mới."
MSG_ERROR = "Xin lỗi, đã có lỗi khi xử lý câu hỏi. Vui lòng thử lại sau."
MSG_UNAVAILABLE = "Trợ lý tạm thời không hoạt động."


class TelegramService:
    def __init__(
        self,
        repo: TelegramBotRepositoryPort,
        agents: AgentService,
        conversations: ConversationRepositoryPort,
        runner: AgentRunnerPort,
        client: TelegramClientPort,
        vault: SecretVaultPort,
        registry: PollRegistryPort,
        limiter: RateLimiterPort,
        audit: AuditService,
        public_base_url: str,
    ) -> None:
        self._repo = repo
        self._agents = agents
        self._conversations = conversations
        self._runner = runner
        self._client = client
        self._vault = vault
        self._registry = registry
        self._limiter = limiter
        self._audit = audit
        self._base = public_base_url.rstrip("/")

    # ------------------------------------------------------------------ quản lý
    async def _editable(self, principal: Principal, agent_id: UUID) -> Any:
        agent = await self._agents.get(principal, agent_id)
        if not self._agents.can_edit(principal, agent):
            raise ForbiddenError("Bạn không có quyền publish agent này.")
        if agent.published_version is None:
            raise ValidationError("Agent chưa được publish.")
        return agent

    async def _bot_for_edit(self, principal: Principal, bot_id: UUID) -> TelegramBot:
        bot = await self._repo.get(principal.tenant_id, bot_id)
        if bot is None:
            raise NotFoundError("Không tìm thấy bot.")
        await self._editable(principal, bot.agent_id)
        return bot

    async def _check_chats(
        self, principal: Principal, agent_id: UUID, published: int, chats: list[int]
    ) -> list[int]:
        if len(chats) > MAX_ALLOWED_CHATS:
            raise ValidationError(f"Tối đa {MAX_ALLOWED_CHATS} chat được phép.")
        if not chats:
            version = await self._agents.version(principal, agent_id, published)
            if version.spec.data_classification.value != "public":
                raise ValidationError(
                    "Agent không ở mức dữ liệu public → phải giới hạn danh sách chat được phép."
                )
        return sorted(set(chats))

    def webhook_url(self, bot: TelegramBot) -> str:
        return f"{self._base}/api/v1/public/telegram/{bot.tenant_id}/{bot.id}"

    async def list_bots(self, principal: Principal, agent_id: UUID) -> list[TelegramBot]:
        await self._editable(principal, agent_id)
        return await self._repo.list_for_agent(principal.tenant_id, agent_id)

    async def create_bot(
        self,
        principal: Principal,
        agent_id: UUID,
        *,
        name: str,
        token: str,
        mode: str = "polling",
        allowed_chat_ids: list[int] | None = None,
        rate_limit_per_min: int = 20,
    ) -> TelegramBot:
        agent = await self._editable(principal, agent_id)
        token = token.strip()
        if not TOKEN_RE.match(token):
            raise ValidationError("Bot token không đúng định dạng (lấy từ @BotFather).")
        if mode not in MODES:
            raise ValidationError("Chế độ phải là webhook hoặc polling.")
        if mode == "webhook" and not self._base.startswith("https://"):
            raise ValidationError("Webhook cần PUBLIC_BASE_URL dạng https — hãy dùng polling.")
        if not 1 <= rate_limit_per_min <= 120:
            raise ValidationError("Giới hạn tốc độ phải từ 1 đến 120 tin/phút.")
        chats = await self._check_chats(
            principal, agent_id, agent.published_version, allowed_chat_ids or []
        )
        try:
            me = await self._client.get_me(token)
        except TelegramError as exc:
            raise ValidationError(f"Telegram từ chối token: {exc}") from None

        secret = new_secret()
        bot = TelegramBot(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            name=name.strip()[:100] or me.username,
            bot_user_id=me.id,
            bot_username=me.username,
            mode=mode,
            webhook_secret_hash=hash_secret(secret),
            enabled=True,
            allowed_chat_ids=chats,
            rate_limit_per_min=rate_limit_per_min,
            update_offset=0,
            created_by=principal.subject,
            created_at=utcnow(),
            last_used_at=None,
        )
        await self._vault.put(secret_path(bot.tenant_id, bot.id), {"bot_token": token})
        try:
            created = await self._repo.create(bot)
        except Exception:
            await self._vault.delete(secret_path(bot.tenant_id, bot.id))
            raise
        try:
            if mode == "webhook":
                await self._client.set_webhook(token, self.webhook_url(created), secret)
            else:
                await self._client.delete_webhook(token)
                await self._registry.add(created.tenant_id, created.id)
        except TelegramError as exc:
            await self._remove(created, token)
            raise ValidationError(f"Không cấu hình được bot: {exc}") from None
        await self._record(principal, "telegram.created", created)
        return created

    async def update_bot(
        self,
        principal: Principal,
        bot_id: UUID,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        allowed_chat_ids: list[int] | None = None,
        rate_limit_per_min: int | None = None,
    ) -> TelegramBot:
        bot = await self._bot_for_edit(principal, bot_id)
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name.strip()[:100] or bot.bot_username
        if enabled is not None:
            changes["enabled"] = enabled
        if rate_limit_per_min is not None:
            if not 1 <= rate_limit_per_min <= 120:
                raise ValidationError("Giới hạn tốc độ phải từ 1 đến 120 tin/phút.")
            changes["rate_limit_per_min"] = rate_limit_per_min
        if allowed_chat_ids is not None:
            agent = await self._editable(principal, bot.agent_id)
            changes["allowed_chat_ids"] = await self._check_chats(
                principal, bot.agent_id, agent.published_version, allowed_chat_ids
            )
        saved = await self._repo.save(replace(bot, **changes))
        if saved.mode == "polling":  # tự đăng ký lại nếu Redis mất danh sách polling
            await self._registry.add(saved.tenant_id, saved.id)
        await self._record(principal, "telegram.updated", saved)
        return saved

    async def delete_bot(self, principal: Principal, bot_id: UUID) -> None:
        bot = await self._bot_for_edit(principal, bot_id)
        token = await self._token(bot)
        await self._remove(bot, token)
        await self._record(principal, "telegram.deleted", bot)

    async def _remove(self, bot: TelegramBot, token: str | None) -> None:
        if token:
            try:
                await self._client.delete_webhook(token)
            except TelegramError:
                logger.warning("Không gỡ được webhook Telegram", extra={"bot_id": str(bot.id)})
        await self._registry.remove(bot.tenant_id, bot.id)
        await self._repo.delete(bot.tenant_id, bot.id)
        await self._vault.delete(secret_path(bot.tenant_id, bot.id))

    async def _record(self, principal: Principal, action: str, bot: TelegramBot) -> None:
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action=action,
            outcome=Outcome.SUCCESS,
            resource_type="telegram_bot",
            resource_id=str(bot.id),
            details={"agent_id": str(bot.agent_id), "mode": bot.mode, "bot": bot.bot_username},
        )

    # ------------------------------------------------------------------ nhận update
    async def _token(self, bot: TelegramBot) -> str | None:
        data = await self._vault.get(secret_path(bot.tenant_id, bot.id))
        return data.get("bot_token") or None

    async def verify_webhook(self, tenant_id: str, bot_id: UUID, secret: str | None) -> TelegramBot:
        bot = await self._repo.get(tenant_id, bot_id)
        if (
            bot is None
            or bot.mode != "webhook"
            or not secret
            or not hmac.compare_digest(bot.webhook_secret_hash, hash_secret(secret))
        ):
            raise UnauthorizedError()
        return bot

    async def handle_update(self, bot: TelegramBot, update: dict[str, Any]) -> None:
        """Xử lý một update; không bao giờ ném lỗi ra ngoài (Telegram sẽ gửi lại vô hạn)."""
        update_id = update.get("update_id")
        if isinstance(update_id, int) and not await self._limiter.hit(
            f"tgupd:{bot.id}:{update_id}", 1, 86400
        ):
            return  # Telegram gửi lại update đã xử lý
        try:
            await self._handle(bot, update)
        except Exception:
            logger.exception("Lỗi xử lý update Telegram", extra={"bot_id": str(bot.id)})

    async def _handle(self, bot: TelegramBot, update: dict[str, Any]) -> None:
        msg = update.get("message")
        if not isinstance(msg, dict):
            return
        chat = msg.get("chat")
        if not isinstance(chat, dict) or chat.get("type") != "private":
            return
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return
        token = await self._token(bot)
        if token is None:
            return

        async def reply(text: str) -> None:
            for part in split_message(text) or ["…"]:
                await self._client.send_message(token, chat_id, part)

        if not bot.enabled:
            await reply(MSG_UNAVAILABLE)
            return
        if bot.allowed_chat_ids and chat_id not in bot.allowed_chat_ids:
            await reply(MSG_NOT_ALLOWED.format(chat_id=chat_id))
            return
        text = msg.get("text")
        if not isinstance(text, str) or not text.strip():
            await reply(MSG_TEXT_ONLY)
            return
        if not await self._limiter.hit(f"telegram:{bot.id}:{chat_id}", bot.rate_limit_per_min, 60):
            await reply(MSG_RATE)
            return

        principal = self.principal(bot, chat_id)
        command = text.strip().split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if command in ("/start", "/new"):
            await self._new_thread(principal, bot)
            _agent, version = await self._agents.resolve_for_run(
                principal, bot.agent_id, draft=False
            )
            await reply(
                version.spec.opening_statement
                if command == "/start" and version.spec.opening_statement
                else MSG_NEW
            )
            return

        await self._client.send_typing(token, chat_id)
        latest = await self._conversations.list_threads(
            bot.tenant_id, principal.subject, bot.agent_id, 1
        )
        answer = await self._runner.ask(
            principal, bot.agent_id, text.strip()[:8000], latest[0].id if latest else None
        )
        await self._repo.touch(bot.tenant_id, bot.id)
        body = answer.text if answer.text else MSG_ERROR
        sources = {
            str(c.get("title") or c.get("document") or "")
            for c in answer.citations
            if c.get("title") or c.get("document")
        }
        if sources and answer.text:
            body += "\n\nNguồn: " + "; ".join(sorted(sources)[:5])
        await reply(body)

    async def _new_thread(self, principal: Principal, bot: TelegramBot) -> None:
        _agent, version = await self._agents.resolve_for_run(principal, bot.agent_id, draft=False)
        await self._conversations.create_thread(
            bot.tenant_id, principal.subject, bot.agent_id, version.version, False, "Telegram"
        )

    @staticmethod
    def principal(bot: TelegramBot, chat_id: int) -> Principal:
        return Principal(
            subject=f"telegram:{bot.id}:{chat_id}",
            tenant_id=bot.tenant_id,
            username=None,
            email=None,
            roles=frozenset(),
            channel=f"telegram:{bot.id}",
        )

    # ------------------------------------------------------------------ long polling
    async def polling_bots(self) -> list[tuple[str, UUID]]:
        return await self._registry.members()

    async def poll_once(self, tenant_id: str, bot_id: UUID, wait_s: int = 10) -> int:
        """Một vòng getUpdates cho bot polling. Trả số update đã xử lý."""
        bot = await self._repo.get(tenant_id, bot_id)
        if bot is None:
            await self._registry.remove(tenant_id, bot_id)
            return 0
        if bot.mode != "polling":
            return 0
        token = await self._token(bot)
        if token is None:
            return 0
        try:
            updates = await self._client.get_updates(token, bot.update_offset, wait_s)
        except TelegramError as exc:
            logger.warning("getUpdates lỗi: %s", exc, extra={"bot_id": str(bot.id)})
            return 0
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                bot = replace(bot, update_offset=max(bot.update_offset, update_id + 1))
                await self._repo.set_offset(
                    bot.tenant_id, bot.id, bot.update_offset
                )  # trước khi xử lý
            await self.handle_update(bot, update)
        return len(updates)
