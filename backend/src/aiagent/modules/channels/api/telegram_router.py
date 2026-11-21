"""API Telegram (ADR-0020 §5): quản lý bot (đăng nhập) + webhook công khai."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Response, status
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.channels.domain.telegram import TelegramBot
from aiagent.modules.identity.domain import Principal

router = APIRouter(prefix="/api/v1", tags=["channels"])
public_router = APIRouter(prefix="/api/v1/public", tags=["public"])

AnyUser = Annotated[Principal, Depends(get_principal)]


class TelegramBotOut(BaseModel):
    id: UUID
    agent_id: UUID
    name: str
    bot_username: str
    mode: str
    enabled: bool
    allowed_chat_ids: list[int]
    rate_limit_per_min: int
    created_by: str
    created_at: datetime
    last_used_at: datetime | None
    webhook_url: str | None


class TelegramBotIn(BaseModel):
    name: str = Field(default="", max_length=100)
    token: str = Field(min_length=10, max_length=100)  # chỉ nhận, không bao giờ trả lại
    mode: str = "polling"
    allowed_chat_ids: list[int] = Field(default_factory=list)
    rate_limit_per_min: int = 20


class TelegramBotPatch(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    allowed_chat_ids: list[int] | None = None
    rate_limit_per_min: int | None = None


def _out(c: Any, bot: TelegramBot) -> TelegramBotOut:
    return TelegramBotOut(
        id=bot.id,
        agent_id=bot.agent_id,
        name=bot.name,
        bot_username=bot.bot_username,
        mode=bot.mode,
        enabled=bot.enabled,
        allowed_chat_ids=bot.allowed_chat_ids,
        rate_limit_per_min=bot.rate_limit_per_min,
        created_by=bot.created_by,
        created_at=bot.created_at,
        last_used_at=bot.last_used_at,
        webhook_url=c.telegram.webhook_url(bot) if bot.mode == "webhook" else None,
    )


@router.get("/agents/{agent_id}/telegram-bots", response_model=list[TelegramBotOut])
async def list_bots(agent_id: UUID, principal: AnyUser, c: ContainerDep) -> list[TelegramBotOut]:
    return [_out(c, b) for b in await c.telegram.list_bots(principal, agent_id)]


@router.post(
    "/agents/{agent_id}/telegram-bots",
    response_model=TelegramBotOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_bot(
    agent_id: UUID, body: TelegramBotIn, principal: AnyUser, c: ContainerDep
) -> TelegramBotOut:
    bot = await c.telegram.create_bot(
        principal,
        agent_id,
        name=body.name,
        token=body.token,
        mode=body.mode,
        allowed_chat_ids=body.allowed_chat_ids,
        rate_limit_per_min=body.rate_limit_per_min,
    )
    return _out(c, bot)


@router.patch("/telegram-bots/{bot_id}", response_model=TelegramBotOut)
async def update_bot(
    bot_id: UUID, body: TelegramBotPatch, principal: AnyUser, c: ContainerDep
) -> TelegramBotOut:
    bot = await c.telegram.update_bot(
        principal,
        bot_id,
        name=body.name,
        enabled=body.enabled,
        allowed_chat_ids=body.allowed_chat_ids,
        rate_limit_per_min=body.rate_limit_per_min,
    )
    return _out(c, bot)


@router.delete("/telegram-bots/{bot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot(bot_id: UUID, principal: AnyUser, c: ContainerDep) -> Response:
    await c.telegram.delete_bot(principal, bot_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@public_router.post("/telegram/{tenant_id}/{bot_id}", status_code=status.HTTP_200_OK)
async def webhook(
    tenant_id: str,
    bot_id: UUID,
    update: dict[str, Any],
    background: BackgroundTasks,
    c: ContainerDep,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    """Telegram gọi vào đây; trả 200 ngay, xử lý nền (Telegram chờ lâu sẽ gửi lại)."""
    bot = await c.telegram.verify_webhook(tenant_id, bot_id, x_telegram_bot_api_secret_token)
    background.add_task(c.telegram.handle_update, bot, update)
    return {"ok": True}
