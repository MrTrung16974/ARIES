"""Fake cho kho tri thức (ADR-0019): repo trong RAM, embedder băm từ (xác định), hàng đợi chạy ngay."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from aiagent.modules.knowledge.domain import (
    EMBEDDING_DIM,
    ChunkDraft,
    ChunkHit,
    DocStatus,
    Document,
    KnowledgeBase,
)
from aiagent.shared_kernel.errors import AppError, ConflictError


def _norm(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.findall(r"\w{2,}", text)


class HashEmbedder:
    """Túi từ băm vào 1024 chiều, chuẩn hóa — đủ để kiểm luồng tìm kiếm theo nghĩa thô."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise AppError("Dịch vụ tìm kiếm tri thức chưa sẵn sàng.")
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * EMBEDDING_DIM
            for w in _norm(t):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % EMBEDDING_DIM] += 1.0  # noqa: S324
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class OverlapReranker:
    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        q = set(_norm(query))
        return [len(q & set(_norm(t))) / (len(q) or 1) for t in texts]


class MemoryBlobs:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.data[key] = data

    async def get(self, key: str) -> bytes:
        return self.data[key]

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class InlineQueue:
    """Chạy ingest ngay (thay worker arq) — gán `service` sau khi tạo KnowledgeService."""

    def __init__(self) -> None:
        self.service: Any = None
        self.enqueued: list[UUID] = []

    async def enqueue_ingest(self, tenant_id: str, doc_id: UUID) -> None:
        self.enqueued.append(doc_id)
        if self.service is not None:
            await self.service.ingest(tenant_id, doc_id)


class InMemoryKnowledgeRepository:
    def __init__(self) -> None:
        self.kbs: dict[UUID, KnowledgeBase] = {}
        self.docs: dict[UUID, Document] = {}
        self.chunks: dict[UUID, list[tuple[UUID, ChunkDraft]]] = {}  # doc_id → (chunk_id, draft)

    def _kb(self, kb: KnowledgeBase) -> KnowledgeBase:
        docs = [d for d in self.docs.values() if d.kb_id == kb.id]
        n = sum(len(self.chunks.get(d.id, [])) for d in docs)
        return replace(kb, doc_count=len(docs), chunk_count=n)

    async def create_kb(self, kb: KnowledgeBase) -> KnowledgeBase:
        if any(k.name == kb.name and k.tenant_id == kb.tenant_id for k in self.kbs.values()):
            raise ConflictError("Tên kho tri thức đã tồn tại.")
        self.kbs[kb.id] = kb
        return self._kb(kb)

    async def get_kb(self, tenant_id: str, kb_id: UUID) -> KnowledgeBase | None:
        kb = self.kbs.get(kb_id)
        return self._kb(kb) if kb and kb.tenant_id == tenant_id else None

    async def list_kbs(self, tenant_id: str) -> list[KnowledgeBase]:
        return [self._kb(k) for k in self.kbs.values() if k.tenant_id == tenant_id]

    async def save_kb(self, kb: KnowledgeBase) -> KnowledgeBase:
        self.kbs[kb.id] = kb
        return self._kb(kb)

    async def delete_kb(self, tenant_id: str, kb_id: UUID) -> None:
        self.kbs.pop(kb_id, None)
        for d in [d for d in self.docs.values() if d.kb_id == kb_id]:
            self.docs.pop(d.id)
            self.chunks.pop(d.id, None)

    async def create_document(self, doc: Document) -> Document:
        if any(d.kb_id == doc.kb_id and d.sha256 == doc.sha256 for d in self.docs.values()):
            raise ConflictError("Tài liệu đã tồn tại.")
        self.docs[doc.id] = doc
        return doc

    async def get_document(self, tenant_id: str, doc_id: UUID) -> Document | None:
        d = self.docs.get(doc_id)
        if d is None or d.tenant_id != tenant_id:
            return None
        return replace(d, chunk_count=len(self.chunks.get(d.id, [])))

    async def list_documents(self, tenant_id: str, kb_id: UUID) -> list[Document]:
        return [
            replace(d, chunk_count=len(self.chunks.get(d.id, [])))
            for d in self.docs.values()
            if d.kb_id == kb_id
        ]

    async def set_status(
        self,
        tenant_id: str,
        doc_id: UUID,
        status: DocStatus,
        *,
        error: str | None = None,
        title: str | None = None,
    ) -> None:
        d = self.docs[doc_id]
        self.docs[doc_id] = replace(d, status=status, error=error, title=title or d.title)

    async def replace_chunks(self, tenant_id: str, doc_id: UUID, chunks: list[ChunkDraft]) -> int:
        self.chunks[doc_id] = [(uuid4(), c) for c in chunks]
        return len(chunks)

    async def delete_document(self, tenant_id: str, doc_id: UUID) -> None:
        self.docs.pop(doc_id, None)
        self.chunks.pop(doc_id, None)

    def _hits(self, kb_ids: list[UUID]) -> list[tuple[Document, UUID, ChunkDraft]]:
        return [
            (d, cid, c)
            for d in self.docs.values()
            if d.kb_id in kb_ids and d.status is DocStatus.READY
            for cid, c in self.chunks.get(d.id, [])
        ]

    @staticmethod
    def _hit(d: Document, cid: UUID, c: ChunkDraft, score: float) -> ChunkHit:
        return ChunkHit(
            cid, d.id, d.kb_id, d.title or d.filename, d.filename, c.text, c.heading, c.page, score
        )

    async def search_vector(
        self, tenant_id: str, kb_ids: list[UUID], embedding: list[float], k: int
    ) -> list[ChunkHit]:
        scored = [
            self._hit(d, cid, c, sum(a * b for a, b in zip(embedding, c.embedding, strict=True)))
            for d, cid, c in self._hits(kb_ids)
        ]
        return sorted(scored, key=lambda h: -h.score)[:k]

    async def search_keyword(
        self, tenant_id: str, kb_ids: list[UUID], query: str, k: int
    ) -> list[ChunkHit]:
        q = set(_norm(query))
        scored = []
        for d, cid, c in self._hits(kb_ids):
            overlap = len(q & set(_norm(c.heading + " " + c.text)))
            if overlap:
                scored.append(self._hit(d, cid, c, float(overlap)))
        return sorted(scored, key=lambda h: -h.score)[:k]
