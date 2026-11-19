"""Alembic chạy bằng role owner (AIAGENT_MIGRATOR_DATABASE_URL), không phải role ứng dụng."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from aiagent.bootstrap.settings import get_settings

config = context.config


def _url() -> str:
    settings = get_settings()
    if settings.migrator_database_url is None:
        raise RuntimeError("Thiếu AIAGENT_MIGRATOR_DATABASE_URL")
    return settings.migrator_database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
