"""Kho tri thức trên Postgres thật: pgvector + full-text bỏ dấu + RLS (ADR-0019)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from aiagent.adapters.db import Database
from aiagent.modules.knowledge.domain import (
    EMBEDDING_DIM,
    ChunkDraft,
    DocStatus,
    Document,
    KbScope,
    KnowledgeBase,
)
from aiagent.modules.knowledge.infrastructure.sql_knowledge_repository import (
    SqlKnowledgeRepository,
)
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import ConflictError

pytestmark = pytest.mark.integration
DB_URL = os.environ.get("AIAGENT_TEST_DATABASE_URL")


@pytest.fixture
async def db() -> AsyncIterator[Database]:
    if not DB_URL:
        pytest.skip("Chưa đặt AIAGENT_TEST_DATABASE_URL")
    database = Database(DB_URL, pool_size=2)
    yield database
    await database.dispose()


def _unit(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIM
    v[i] = 1.0
    return v


async def test_knowledge_repository_vector_keyword_and_rls(db: Database) -> None:
    repo = SqlKnowledgeRepository(db)
    now = utcnow()
    kb = await repo.create_kb(
        KnowledgeBase(
            uuid4(),
            "default",
            f"IT KB {uuid4().hex[:6]}",
            "",
            KbScope.PERSONAL,
            "it-user",
            "internal",
            0,
            0,
            now,
            now,
        )
    )
    doc = await repo.create_document(
        Document(
            uuid4(),
            "default",
            kb.id,
            "so-tay.md",
            "text/markdown",
            10,
            uuid4().hex,
            DocStatus.PENDING,
            None,
            "",
            0,
            "it-user",
            now,
            now,
        )
    )
    with pytest.raises(ConflictError):  # cùng nội dung (sha256) trong một KB
        await repo.create_document(
            Document(
                uuid4(),
                "default",
                kb.id,
                "x.md",
                "text/markdown",
                10,
                doc.sha256,
                DocStatus.PENDING,
                None,
                "",
                0,
                "it-user",
                now,
                now,
            )
        )
    n = await repo.replace_chunks(
        "default",
        doc.id,
        [
            ChunkDraft(0, "Nhân viên được 12 ngày nghỉ phép mỗi năm.", "Nghỉ phép", 1, _unit(3)),
            ChunkDraft(1, "Đi công tác được thanh toán theo hóa đơn.", "Công tác", 2, _unit(7)),
        ],
    )
    assert n == 2
    # Chưa "ready" → không xuất hiện trong tìm kiếm
    assert await repo.search_vector("default", [kb.id], _unit(3), 5) == []
    await repo.set_status("default", doc.id, DocStatus.READY, title="Sổ tay")

    hits = await repo.search_vector("default", [kb.id], _unit(3), 5)
    assert hits[0].heading == "Nghỉ phép" and hits[0].document_title == "Sổ tay"
    assert hits[0].score == pytest.approx(1.0)

    # Từ khóa không dấu vẫn khớp nội dung có dấu (unaccent), đ → d
    kw = await repo.search_keyword("default", [kb.id], "nghi phep", 5)
    assert [h.heading for h in kw] == ["Nghỉ phép"]
    kw = await repo.search_keyword("default", [kb.id], "di cong tac", 5)
    assert kw[0].heading == "Công tác"
    assert await repo.search_keyword("default", [kb.id], "!!! ' ; drop", 5) == []

    listed = await repo.get_kb("default", kb.id)
    assert listed is not None and (listed.doc_count, listed.chunk_count) == (1, 2)
    assert await repo.get_kb("other-tenant", kb.id) is None  # RLS

    await repo.delete_document("default", doc.id)
    assert await repo.search_keyword("default", [kb.id], "nghi phep", 5) == []
    await repo.delete_kb("default", kb.id)
    assert await repo.get_kb("default", kb.id) is None
