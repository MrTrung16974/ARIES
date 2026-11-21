from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from arq import cron, run_worker
from arq.connections import RedisSettings

from aiagent.bootstrap.container import build_container
from aiagent.bootstrap.logging import configure_logging
from aiagent.bootstrap.settings import get_settings

logger = logging.getLogger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    container = build_container(settings)
    await container.start()
    ctx["container"] = container
    logger.info("Worker started")


async def shutdown(ctx: dict[str, Any]) -> None:
    container = ctx.get("container")
    if container is not None:
        await container.stop()


async def ping(ctx: dict[str, Any]) -> str:
    """Job kiểm tra hàng đợi hoạt động."""
    return "pong"


async def ingest_document(ctx: dict[str, Any], tenant_id: str, doc_id: str) -> str:
    """Parse → chunk → embed → lưu chunk cho một tài liệu (ADR-0019)."""
    status = await ctx["container"].knowledge.ingest(tenant_id, UUID(doc_id))
    return str(status)


async def telegram_poll(ctx: dict[str, Any]) -> int:
    """Long polling cho bot Telegram không nhận được webhook (ADR-0020 §5).

    Cron mỗi phút, chạy ~50 giây: lần lượt getUpdates từng bot (timeout chia đều).
    """
    service = ctx["container"].telegram
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 50
    handled = 0
    while loop.time() < deadline:
        bots = await service.polling_bots()
        if not bots:
            break
        wait = max(1, min(20, int(deadline - loop.time()) // len(bots)))
        for tenant_id, bot_id in bots:
            if loop.time() >= deadline:
                break
            try:
                handled += await service.poll_once(tenant_id, bot_id, wait_s=wait)
            except Exception:
                logger.exception("Polling Telegram lỗi", extra={"bot_id": str(bot_id)})
    return handled


async def schedule_tick(ctx: dict[str, Any]) -> int:
    """Mỗi phút: nhận lịch chạy đến hạn ở mọi tenant và đẩy job `run_schedule` (ADR-0021)."""
    return len(await ctx["container"].automation.tick())


async def run_schedule(ctx: dict[str, Any], tenant_id: str, schedule_id: str, fired_at: str) -> int:
    """Chạy agent cho một lần kích hoạt lịch (cron) hoặc kiểm tra mail mới (gmail)."""
    return int(
        await ctx["container"].automation.execute(
            tenant_id, UUID(schedule_id), datetime.fromisoformat(fired_at)
        )
    )


async def heartbeat(ctx: dict[str, Any]) -> None:
    checks = await ctx["container"].readiness()
    logger.info("Worker heartbeat", extra={"checks": checks})


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping, ingest_document, run_schedule]
    job_timeout = 900  # tài liệu lớn trên CPU có thể mất vài phút
    max_jobs = 4  # ingest, lịch chạy, polling Telegram
    cron_jobs: ClassVar[list[Any]] = [
        cron(heartbeat, minute=set(range(0, 60, 5))),
        cron(telegram_poll, second=0, unique=True, timeout=58),
        cron(schedule_tick, second=5, unique=True, timeout=50),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())


def run() -> None:
    run_worker(WorkerSettings)  # type: ignore[arg-type]
