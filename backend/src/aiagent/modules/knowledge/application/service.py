"""Knowledge base (ADR-0019): quyền, tải tài liệu, ingest nền, tìm kiếm lai + rerank.

Quyền:
- Đọc (dùng trong agent): KB `workspace`, hoặc KB `personal` của chính mình.
- Quản lý (sửa, tải/xóa tài liệu): Admin, hoặc chủ của KB `personal`.
- Tạo: Builder trở lên; KB `workspace` chỉ Admin.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.identity.domain import Principal, Role
from aiagent.modules.knowledge.application.chunking import chunk_sections
from aiagent.modules.knowledge.application.parsing import MAX_BYTES, detect_type, parse
from aiagent.modules.knowledge.domain import (
    BlobStorePort,
    ChunkDraft,
    ChunkHit,
    DocStatus,
    Document,
    EmbedderPort,
    IngestQueuePort,
    KbScope,
    KnowledgeBase,
    KnowledgeRepositoryPort,
    RerankerPort,
    blob_key,
)
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
EMBED_BATCH = 16
CANDIDATES = 20  # mỗi nhánh (vector, từ khóa) trước khi gộp
RERANK_POOL = 20
RRF_K = 60


def rrf_merge(*ranked: list[ChunkHit], k: int = RRF_K) -> list[ChunkHit]:
    """Reciprocal Rank Fusion: gộp nhiều danh sách xếp hạng, không cần chuẩn hóa điểm."""
    scores: dict[UUID, float] = {}
    best: dict[UUID, ChunkHit] = {}
    for hits in ranked:
        for rank, hit in enumerate(hits):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            best.setdefault(hit.chunk_id, hit)
    order = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [replace(best[cid], score=scores[cid]) for cid in order]


class KnowledgeService:
    def __init__(
        self,
        repo: KnowledgeRepositoryPort,
        blobs: BlobStorePort,
        embedder: EmbedderPort,
        reranker: RerankerPort | None,
        queue: IngestQueuePort,
        audit: AuditService,
    ) -> None:
        self._repo = repo
        self._blobs = blobs
        self._embedder = embedder
        self._reranker = reranker
        self._queue = queue
        self._audit = audit

    # ------------------------------------------------------------------ quyền
    @staticmethod
    def can_read(principal: Principal, kb: KnowledgeBase) -> bool:
        return kb.scope is KbScope.WORKSPACE or kb.owner_id == principal.subject

    @staticmethod
    def can_manage(principal: Principal, kb: KnowledgeBase) -> bool:
        if principal.has_any_role(Role.ADMIN):
            return True
        return kb.scope is KbScope.PERSONAL and kb.owner_id == principal.subject

    def _visible(self, principal: Principal, kb: KnowledgeBase) -> bool:
        return self.can_read(principal, kb) or self.can_manage(principal, kb)

    async def get_kb(self, principal: Principal, kb_id: UUID) -> KnowledgeBase:
        kb = await self._repo.get_kb(principal.tenant_id, kb_id)
        if kb is None or not self._visible(principal, kb):
            raise NotFoundError("Không tìm thấy kho tri thức.")
        return kb

    async def _managed(self, principal: Principal, kb_id: UUID) -> KnowledgeBase:
        kb = await self.get_kb(principal, kb_id)
        if not self.can_manage(principal, kb):
            raise ForbiddenError("Bạn không có quyền quản lý kho tri thức này.")
        return kb

    # ------------------------------------------------------------------ KB
    async def list_kbs(self, principal: Principal) -> list[KnowledgeBase]:
        return [
            kb
            for kb in await self._repo.list_kbs(principal.tenant_id)
            if self._visible(principal, kb)
        ]

    async def create_kb(
        self,
        principal: Principal,
        *,
        name: str,
        description: str,
        scope: KbScope,
        data_classification: str,
    ) -> KnowledgeBase:
        if not principal.has_any_role(Role.BUILDER, Role.ADMIN):
            raise ForbiddenError("Cần vai trò Builder để tạo kho tri thức.")
        if scope is KbScope.WORKSPACE and not principal.has_any_role(Role.ADMIN):
            raise ForbiddenError("Chỉ Admin tạo được kho tri thức dùng chung.")
        if data_classification not in CLASSIFICATIONS:
            raise ValidationError("Phân loại dữ liệu không hợp lệ.")
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise ValidationError("Tên kho tri thức 1–80 ký tự.")
        now = utcnow()
        kb = await self._repo.create_kb(
            KnowledgeBase(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                name=name,
                description=description.strip()[:1000],
                scope=scope,
                owner_id=principal.subject,
                data_classification=data_classification,
                doc_count=0,
                chunk_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        await self._record(principal, "kb.created", "knowledge_base", kb.id, {"name": kb.name})
        return kb

    async def update_kb(
        self, principal: Principal, kb_id: UUID, *, name: str | None, description: str | None
    ) -> KnowledgeBase:
        kb = await self._managed(principal, kb_id)
        changes: dict[str, Any] = {}
        if name is not None and name.strip():
            changes["name"] = name.strip()[:80]
        if description is not None:
            changes["description"] = description.strip()[:1000]
        if not changes:
            return kb
        return await self._repo.save_kb(replace(kb, **changes, updated_at=utcnow()))

    async def delete_kb(self, principal: Principal, kb_id: UUID) -> None:
        kb = await self._managed(principal, kb_id)
        docs = await self._repo.list_documents(principal.tenant_id, kb_id)
        await self._repo.delete_kb(principal.tenant_id, kb_id)
        for d in docs:
            await self._safe_blob_delete(blob_key(d.tenant_id, d.kb_id, d.id))
        await self._record(principal, "kb.deleted", "knowledge_base", kb.id, {"name": kb.name})

    # ------------------------------------------------------------------ tài liệu
    async def list_documents(self, principal: Principal, kb_id: UUID) -> list[Document]:
        await self.get_kb(principal, kb_id)
        return await self._repo.list_documents(principal.tenant_id, kb_id)

    async def upload(
        self, principal: Principal, kb_id: UUID, *, filename: str, data: bytes
    ) -> Document:
        kb = await self._managed(principal, kb_id)
        filename = (filename or "tai-lieu").replace("\\", "/").rsplit("/", 1)[-1][:200]
        if not data:
            raise ValidationError("Tệp trống.")
        if len(data) > MAX_BYTES:
            raise ValidationError("Tệp quá 25 MB.")
        content_type = detect_type(data, filename)  # từ chối sớm loại không hỗ trợ
        now = utcnow()
        doc = Document(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            kb_id=kb.id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            status=DocStatus.PENDING,
            error=None,
            title="",
            chunk_count=0,
            created_by=principal.subject,
            created_at=now,
            updated_at=now,
        )
        key = blob_key(doc.tenant_id, doc.kb_id, doc.id)
        await self._blobs.put(key, data)
        try:
            created = await self._repo.create_document(doc)
        except ConflictError:
            await self._safe_blob_delete(key)
            raise ConflictError("Tài liệu này đã có trong kho tri thức.") from None
        await self._queue.enqueue_ingest(principal.tenant_id, created.id)
        await self._record(
            principal,
            "document.uploaded",
            "document",
            created.id,
            {"kb_id": str(kb.id), "filename": filename, "bytes": len(data)},
        )
        return created

    async def reingest(self, principal: Principal, doc_id: UUID) -> Document:
        doc = await self._document(principal, doc_id)
        await self._repo.set_status(principal.tenant_id, doc.id, DocStatus.PENDING)
        await self._queue.enqueue_ingest(principal.tenant_id, doc.id)
        return replace(doc, status=DocStatus.PENDING, error=None)

    async def delete_document(self, principal: Principal, doc_id: UUID) -> None:
        doc = await self._document(principal, doc_id)
        await self._repo.delete_document(principal.tenant_id, doc.id)
        await self._safe_blob_delete(blob_key(doc.tenant_id, doc.kb_id, doc.id))
        await self._record(
            principal, "document.deleted", "document", doc.id, {"filename": doc.filename}
        )

    async def _document(self, principal: Principal, doc_id: UUID) -> Document:
        doc = await self._repo.get_document(principal.tenant_id, doc_id)
        if doc is None:
            raise NotFoundError("Không tìm thấy tài liệu.")
        await self._managed(principal, doc.kb_id)
        return doc

    # ------------------------------------------------------------------ ingest (worker)
    async def ingest(self, tenant_id: str, doc_id: UUID) -> DocStatus:
        doc = await self._repo.get_document(tenant_id, doc_id)
        if doc is None:
            return DocStatus.FAILED
        await self._repo.set_status(tenant_id, doc_id, DocStatus.PROCESSING)
        try:
            data = await self._blobs.get(blob_key(tenant_id, doc.kb_id, doc.id))
            _, parsed = parse(data, doc.filename)
            drafts = chunk_sections(parsed.sections)
            if not drafts:
                raise ValidationError("Tài liệu không có nội dung để lập chỉ mục.")
            embedded: list[ChunkDraft] = []
            for i in range(0, len(drafts), EMBED_BATCH):
                batch = drafts[i : i + EMBED_BATCH]
                vectors = await self._embedder.embed(
                    [f"{c.heading}\n{c.text}" if c.heading else c.text for c in batch]
                )
                embedded += [replace(c, embedding=v) for c, v in zip(batch, vectors, strict=True)]
            await self._repo.replace_chunks(tenant_id, doc_id, embedded)
            await self._repo.set_status(tenant_id, doc_id, DocStatus.READY, title=parsed.title)
            return DocStatus.READY
        except AppError as exc:
            message = exc.message
        except Exception:
            logger.exception("Ingest tài liệu %s lỗi", doc_id)
            message = "Lỗi nội bộ khi xử lý tài liệu."
        await self._repo.set_status(tenant_id, doc_id, DocStatus.FAILED, error=message[:500])
        return DocStatus.FAILED

    # ------------------------------------------------------------------ tìm kiếm
    async def readable(self, principal: Principal, kb_ids: list[str]) -> list[KnowledgeBase]:
        out = []
        for raw in kb_ids:
            try:
                kb = await self._repo.get_kb(principal.tenant_id, UUID(raw))
            except ValueError:
                continue
            if kb is not None and self.can_read(principal, kb):
                out.append(kb)
        return out

    async def search(
        self, principal: Principal, kb_ids: list[UUID], query: str, *, top_k: int = 5
    ) -> list[ChunkHit]:
        """Tìm lai: vector (bge-m3) + từ khóa → RRF → rerank (cross-encoder) → top_k."""
        query = query.strip()[:1000]
        if not query or not kb_ids:
            return []
        readable = [kb.id for kb in await self.readable(principal, [str(k) for k in kb_ids])]
        if not readable:
            return []
        tenant = principal.tenant_id
        [vector] = await self._embedder.embed([query])
        by_vector = await self._repo.search_vector(tenant, readable, vector, CANDIDATES)
        by_keyword = await self._repo.search_keyword(tenant, readable, query, CANDIDATES)
        merged = rrf_merge(by_vector, by_keyword)[:RERANK_POOL]
        if self._reranker is not None and len(merged) > 1:
            try:
                scores = await self._reranker.rerank(query, [h.text for h in merged])
                merged = [
                    replace(h, score=s)
                    for h, s in sorted(zip(merged, scores, strict=True), key=lambda x: -x[1])
                ]
            except AppError:
                logger.warning("Reranker lỗi — dùng thứ tự RRF")
        return merged[:top_k]

    # ------------------------------------------------------------------ tiện ích
    async def _safe_blob_delete(self, key: str) -> None:
        try:
            await self._blobs.delete(key)
        except Exception:
            logger.warning("Không xóa được tệp %s", key)

    async def _record(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: UUID,
        details: dict[str, Any],
    ) -> None:
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action=action,
            outcome=Outcome.SUCCESS,
            resource_type=resource_type,
            resource_id=str(resource_id),
            details=details,
        )


__all__ = ["KnowledgeService", "rrf_merge"]
