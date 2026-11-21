"""Dọn dẹp sau test tích hợp: agent do các tài khoản test tạo được đưa vào lưu trữ (ẩn khỏi UI).

Không xóa cứng — thread/run/audit của test vẫn tham chiếu agent (khóa ngoại, chuỗi hash audit).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from aiagent.adapters.db import Database

TEST_OWNERS = ("it-user", "it-owner", "it-builder")
_ARCHIVE = text(
    "UPDATE agents.agents SET status = 'archived' "
    "WHERE owner_id = ANY(:owners) AND status <> 'archived'"
)


@pytest.fixture(autouse=True)
async def _archive_test_agents() -> AsyncIterator[None]:
    yield
    url = os.environ.get("AIAGENT_TEST_DATABASE_URL")
    if not url:
        return
    db = Database(url, pool_size=1)
    try:
        async with db.tenant_session("default") as s:
            await s.execute(_ARCHIVE, {"owners": list(TEST_OWNERS)})
    finally:
        await db.dispose()
