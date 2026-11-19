"""PostgreSQL (async) với cô lập tenant bằng Row-Level Security.

Mọi truy cập dữ liệu nghiệp vụ phải đi qua `Database.tenant_session`, hàm này đặt
`app.tenant_id` trong transaction để các policy RLS lọc dữ liệu (docs/security.md mục 2).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    def __init__(self, url: str, pool_size: int = 10) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_size=pool_size, pool_pre_ping=True)
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def tenant_session(self, tenant_id: str) -> AsyncIterator[AsyncSession]:
        """Session trong một transaction, đã gắn tenant cho RLS. Commit khi thoát bình thường."""
        if not tenant_id:
            raise ValueError("tenant_id bắt buộc")
        async with self._sessions() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            yield session

    async def ping(self) -> bool:
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    async def dispose(self) -> None:
        await self.engine.dispose()
