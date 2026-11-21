from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from aiagent.adapters.db import Database
from aiagent.modules.knowledge.domain import (
    ChunkDraft,
    ChunkHit,
    DocStatus,
    Document,
    KbScope,
    KnowledgeBase,
)
from aiagent.shared_kernel.errors import ConflictError, NotFoundError

_KB_SELECT = """
    SELECT k.id, k.tenant_id, k.name, k.description, k.scope, k.owner_id, k.data_classification,
           k.created_at, k.updated_at,
           (SELECT count(*) FROM knowledge.documents d WHERE d.kb_id = k.id) AS doc_count,
           (SELECT count(*) FROM knowledge.chunks c WHERE c.kb_id = k.id) AS chunk_count
      FROM knowledge.kbs k
"""
_GET_KB = text(_KB_SELECT + " WHERE k.id = :id")
_LIST_KB = text(_KB_SELECT + " ORDER BY k.name")
_INSERT_KB = text(
    """
    INSERT INTO knowledge.kbs (id, tenant_id, name, description, scope, owner_id,
                               data_classification)
    VALUES (:id, :tenant_id, :name, :description, :scope, :owner_id, :data_classification)
    """
)
_SAVE_KB = text(
    """
    UPDATE knowledge.kbs SET name = :name, description = :description, updated_at = now()
     WHERE id = :id
    """
)
_DELETE_KB = text("DELETE FROM knowledge.kbs WHERE id = :id")

_DOC_SELECT = """
    SELECT d.id, d.tenant_id, d.kb_id, d.filename, d.content_type, d.size_bytes, d.sha256,
           d.status, d.error, d.title, d.created_by, d.created_at, d.updated_at,
           (SELECT count(*) FROM knowledge.chunks c WHERE c.document_id = d.id) AS chunk_count
      FROM knowledge.documents d
"""
_GET_DOC = text(_DOC_SELECT + " WHERE d.id = :id")
_LIST_DOC = text(_DOC_SELECT + " WHERE d.kb_id = :kb_id ORDER BY d.created_at DESC")
_INSERT_DOC = text(
    """
    INSERT INTO knowledge.documents (id, tenant_id, kb_id, filename, content_type, size_bytes,
                                     sha256, status, created_by)
    VALUES (:id, :tenant_id, :kb_id, :filename, :content_type, :size_bytes, :sha256, 'pending',
            :created_by)
    """
)
_SET_STATUS = text(
    """
    UPDATE knowledge.documents
       SET status = :status, error = :error, title = COALESCE(:title, title), updated_at = now()
     WHERE id = :id
    """
)
_DELETE_CHUNKS = text("DELETE FROM knowledge.chunks WHERE document_id = :id")
_INSERT_CHUNK = text(
    """
    INSERT INTO knowledge.chunks (id, tenant_id, kb_id, document_id, ord, heading, page, text,
                                  embedding)
    SELECT :id, :tenant_id, d.kb_id, d.id, :ord, :heading, :page, :text,
           CAST(:embedding AS vector)
      FROM knowledge.documents d WHERE d.id = :document_id
    """
)
_DELETE_DOC = text("DELETE FROM knowledge.documents WHERE id = :id")

_HIT_COLUMNS = """
    c.id AS chunk_id, c.document_id, c.kb_id, d.title AS document_title, d.filename, c.text,
    c.heading, c.page
"""
_SEARCH_VECTOR = text(
    f"""
    SELECT {_HIT_COLUMNS}, 1 - (c.embedding <=> CAST(:embedding AS vector)) AS score
      FROM knowledge.chunks c JOIN knowledge.documents d ON d.id = c.document_id
     WHERE c.kb_id = ANY(:kb_ids) AND d.status = 'ready'
     ORDER BY c.embedding <=> CAST(:embedding AS vector)
     LIMIT :k
    """  # noqa: S608 - _HIT_COLUMNS là hằng số
)
_SEARCH_KEYWORD = text(
    f"""
    SELECT {_HIT_COLUMNS}, ts_rank_cd(c.tsv, q) AS score
      FROM knowledge.chunks c JOIN knowledge.documents d ON d.id = c.document_id,
           to_tsquery('simple', knowledge.f_unaccent(:tsquery)) q
     WHERE c.kb_id = ANY(:kb_ids) AND d.status = 'ready' AND c.tsv @@ q
     ORDER BY score DESC
     LIMIT :k
    """  # noqa: S608 - _HIT_COLUMNS là hằng số
)
_WORD = re.compile(r"[\w]{2,}", re.UNICODE)


def _vector(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def keyword_query(query: str) -> str:
    """Câu hỏi → tsquery OR các từ (đã lọc ký tự) — tránh lỗi cú pháp và chặn tiêm toán tử."""
    words = list(dict.fromkeys(w.lower() for w in _WORD.findall(query)))[:30]
    return " | ".join(words)


def _kb(m: Any) -> KnowledgeBase:
    return KnowledgeBase(
        id=m["id"],
        tenant_id=m["tenant_id"],
        name=m["name"],
        description=m["description"],
        scope=KbScope(m["scope"]),
        owner_id=m["owner_id"],
        data_classification=m["data_classification"],
        doc_count=int(m["doc_count"]),
        chunk_count=int(m["chunk_count"]),
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def _doc(m: Any) -> Document:
    return Document(
        id=m["id"],
        tenant_id=m["tenant_id"],
        kb_id=m["kb_id"],
        filename=m["filename"],
        content_type=m["content_type"],
        size_bytes=int(m["size_bytes"]),
        sha256=m["sha256"],
        status=DocStatus(m["status"]),
        error=m["error"],
        title=m["title"],
        chunk_count=int(m["chunk_count"]),
        created_by=m["created_by"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


def _hit(m: Any) -> ChunkHit:
    return ChunkHit(
        chunk_id=m["chunk_id"],
        document_id=m["document_id"],
        kb_id=m["kb_id"],
        document_title=m["document_title"] or m["filename"],
        filename=m["filename"],
        text=m["text"],
        heading=m["heading"],
        page=m["page"],
        score=float(m["score"] or 0.0),
    )


class SqlKnowledgeRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ KB
    async def create_kb(self, kb: KnowledgeBase) -> KnowledgeBase:
        try:
            async with self._db.tenant_session(kb.tenant_id) as s:
                await s.execute(
                    _INSERT_KB,
                    {
                        "id": kb.id,
                        "tenant_id": kb.tenant_id,
                        "name": kb.name,
                        "description": kb.description,
                        "scope": kb.scope.value,
                        "owner_id": kb.owner_id,
                        "data_classification": kb.data_classification,
                    },
                )
        except IntegrityError as exc:
            raise ConflictError("Tên kho tri thức đã tồn tại.") from exc
        created = await self.get_kb(kb.tenant_id, kb.id)
        if created is None:
            raise NotFoundError()
        return created

    async def get_kb(self, tenant_id: str, kb_id: UUID) -> KnowledgeBase | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET_KB, {"id": kb_id})).first()
        return _kb(row._mapping) if row else None

    async def list_kbs(self, tenant_id: str) -> list[KnowledgeBase]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST_KB)).all()
        return [_kb(r._mapping) for r in rows]

    async def save_kb(self, kb: KnowledgeBase) -> KnowledgeBase:
        try:
            async with self._db.tenant_session(kb.tenant_id) as s:
                await s.execute(
                    _SAVE_KB, {"id": kb.id, "name": kb.name, "description": kb.description}
                )
        except IntegrityError as exc:
            raise ConflictError("Tên kho tri thức đã tồn tại.") from exc
        saved = await self.get_kb(kb.tenant_id, kb.id)
        if saved is None:
            raise NotFoundError()
        return saved

    async def delete_kb(self, tenant_id: str, kb_id: UUID) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(_DELETE_KB, {"id": kb_id})

    # ------------------------------------------------------------------ tài liệu
    async def create_document(self, doc: Document) -> Document:
        try:
            async with self._db.tenant_session(doc.tenant_id) as s:
                await s.execute(
                    _INSERT_DOC,
                    {
                        "id": doc.id,
                        "tenant_id": doc.tenant_id,
                        "kb_id": doc.kb_id,
                        "filename": doc.filename,
                        "content_type": doc.content_type,
                        "size_bytes": doc.size_bytes,
                        "sha256": doc.sha256,
                        "created_by": doc.created_by,
                    },
                )
        except IntegrityError as exc:
            raise ConflictError("Tài liệu đã tồn tại.") from exc
        created = await self.get_document(doc.tenant_id, doc.id)
        if created is None:
            raise NotFoundError()
        return created

    async def get_document(self, tenant_id: str, doc_id: UUID) -> Document | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET_DOC, {"id": doc_id})).first()
        return _doc(row._mapping) if row else None

    async def list_documents(self, tenant_id: str, kb_id: UUID) -> list[Document]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST_DOC, {"kb_id": kb_id})).all()
        return [_doc(r._mapping) for r in rows]

    async def set_status(
        self,
        tenant_id: str,
        doc_id: UUID,
        status: DocStatus,
        *,
        error: str | None = None,
        title: str | None = None,
    ) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(
                _SET_STATUS,
                {"id": doc_id, "status": status.value, "error": error, "title": title},
            )

    async def replace_chunks(self, tenant_id: str, doc_id: UUID, chunks: list[ChunkDraft]) -> int:
        async with self._db.tenant_session(tenant_id) as s:  # một transaction: không lộ nửa vời
            await s.execute(_DELETE_CHUNKS, {"id": doc_id})
            for c in chunks:
                await s.execute(
                    _INSERT_CHUNK,
                    {
                        "id": uuid4(),
                        "tenant_id": tenant_id,
                        "document_id": doc_id,
                        "ord": c.ord,
                        "heading": c.heading,
                        "page": c.page,
                        "text": c.text,
                        "embedding": _vector(c.embedding),
                    },
                )
        return len(chunks)

    async def delete_document(self, tenant_id: str, doc_id: UUID) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(_DELETE_DOC, {"id": doc_id})

    # ------------------------------------------------------------------ tìm kiếm
    async def search_vector(
        self, tenant_id: str, kb_ids: list[UUID], embedding: list[float], k: int
    ) -> list[ChunkHit]:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(text("SET LOCAL hnsw.ef_search = 100"))
            rows = (
                await s.execute(
                    _SEARCH_VECTOR,
                    {"embedding": _vector(embedding), "kb_ids": kb_ids, "k": k},
                )
            ).all()
        return [_hit(r._mapping) for r in rows]

    async def search_keyword(
        self, tenant_id: str, kb_ids: list[UUID], query: str, k: int
    ) -> list[ChunkHit]:
        tsquery = keyword_query(query)
        if not tsquery:
            return []
        async with self._db.tenant_session(tenant_id) as s:
            rows = (
                await s.execute(_SEARCH_KEYWORD, {"tsquery": tsquery, "kb_ids": kb_ids, "k": k})
            ).all()
        return [_hit(r._mapping) for r in rows]
