"""Knowledge base (RAG) — ADR-0019."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

EMBEDDING_DIM = 1024  # BAAI/bge-m3


class KbScope(StrEnum):
    PERSONAL = "personal"
    WORKSPACE = "workspace"


class DocStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True)
class KnowledgeBase:
    id: UUID
    tenant_id: str
    name: str
    description: str
    scope: KbScope
    owner_id: str
    data_classification: str  # public | internal | confidential | restricted
    doc_count: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Document:
    id: UUID
    tenant_id: str
    kb_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    status: DocStatus
    error: str | None
    title: str
    chunk_count: int
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Section:
    """Một đoạn liền mạch sau khi parse (giữ tiêu đề/trang để trích dẫn)."""

    text: str
    heading: str = ""
    page: int | None = None


@dataclass(frozen=True)
class ParsedDocument:
    title: str
    sections: list[Section]


@dataclass(frozen=True)
class ChunkDraft:
    ord: int
    text: str
    heading: str = ""
    page: int | None = None
    embedding: list[float] = field(default_factory=list, repr=False)


@dataclass(frozen=True)
class ChunkHit:
    chunk_id: UUID
    document_id: UUID
    kb_id: UUID
    document_title: str
    filename: str
    text: str
    heading: str
    page: int | None
    score: float


class KnowledgeRepositoryPort(Protocol):
    async def create_kb(self, kb: KnowledgeBase) -> KnowledgeBase: ...

    async def get_kb(self, tenant_id: str, kb_id: UUID) -> KnowledgeBase | None: ...

    async def list_kbs(self, tenant_id: str) -> list[KnowledgeBase]: ...

    async def save_kb(self, kb: KnowledgeBase) -> KnowledgeBase: ...

    async def delete_kb(self, tenant_id: str, kb_id: UUID) -> None: ...

    async def create_document(self, doc: Document) -> Document: ...

    async def get_document(self, tenant_id: str, doc_id: UUID) -> Document | None: ...

    async def list_documents(self, tenant_id: str, kb_id: UUID) -> list[Document]: ...

    async def set_status(
        self,
        tenant_id: str,
        doc_id: UUID,
        status: DocStatus,
        *,
        error: str | None = None,
        title: str | None = None,
    ) -> None: ...

    async def replace_chunks(self, tenant_id: str, doc_id: UUID, chunks: list[ChunkDraft]) -> int:
        """Xóa chunk cũ của tài liệu, ghi chunk mới; trả số chunk."""
        ...

    async def delete_document(self, tenant_id: str, doc_id: UUID) -> None: ...

    async def search_vector(
        self, tenant_id: str, kb_ids: list[UUID], embedding: list[float], k: int
    ) -> list[ChunkHit]: ...

    async def search_keyword(
        self, tenant_id: str, kb_ids: list[UUID], query: str, k: int
    ) -> list[ChunkHit]: ...


class BlobStorePort(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...


class EmbedderPort(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class RerankerPort(Protocol):
    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        """Điểm liên quan cho từng đoạn, cùng thứ tự với `texts`."""
        ...


class IngestQueuePort(Protocol):
    async def enqueue_ingest(self, tenant_id: str, doc_id: UUID) -> None: ...


def blob_key(tenant_id: str, kb_id: UUID, doc_id: UUID) -> str:
    return f"{tenant_id}/{kb_id}/{doc_id}"


__all__ = [
    "EMBEDDING_DIM",
    "BlobStorePort",
    "ChunkDraft",
    "ChunkHit",
    "DocStatus",
    "Document",
    "EmbedderPort",
    "IngestQueuePort",
    "KbScope",
    "KnowledgeBase",
    "KnowledgeRepositoryPort",
    "ParsedDocument",
    "RerankerPort",
    "Section",
    "blob_key",
]
