from __future__ import annotations

import logging
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


async def heartbeat(ctx: dict[str, Any]) -> None:
    checks = await ctx["container"].readiness()
    logger.info("Worker heartbeat", extra={"checks": checks})


class WorkerSettings:
    functions: ClassVar[list[Any]] = [ping, ingest_document]
    job_timeout = 900  # tài liệu lớn trên CPU có thể mất vài phút
    max_jobs = 2
    cron_jobs: ClassVar[list[Any]] = [cron(heartbeat, minute=set(range(0, 60, 5)))]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())


def run() -> None:
    run_worker(WorkerSettings)  # type: ignore[arg-type]
