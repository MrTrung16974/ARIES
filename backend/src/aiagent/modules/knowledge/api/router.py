"""API kho tri thức (ADR-0019)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from pydantic import BaseModel, Field

from aiagent.api.deps import ContainerDep, get_principal
from aiagent.modules.identity.domain import Principal
from aiagent.modules.knowledge.application import format_hits
from aiagent.modules.knowledge.application.parsing import MAX_BYTES
from aiagent.modules.knowledge.domain import Document, KbScope, KnowledgeBase
from aiagent.shared_kernel.errors import ValidationError

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])

AnyUser = Annotated[Principal, Depends(get_principal)]


class KbOut(BaseModel):
    id: UUID
    name: str
    description: str
    scope: KbScope
    owner_id: str
    data_classification: str
    doc_count: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime
    can_manage: bool
    can_read: bool


class CreateKbIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    scope: KbScope = KbScope.PERSONAL
    data_classification: str = Field(default="internal", max_length=20)


class UpdateKbIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=1000)


class DocumentOut(BaseModel):
    id: UUID
    kb_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    status: str
    error: str | None
    title: str
    chunk_count: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class SearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


class SearchOut(BaseModel):
    text: str
    citations: list[dict[str, Any]]
    scores: list[float]


def _kb(c: Any, p: Principal, kb: KnowledgeBase) -> KbOut:
    svc = c.knowledge
    return KbOut(
        **{k: getattr(kb, k) for k in KbOut.model_fields if hasattr(kb, k)},
        can_manage=svc.can_manage(p, kb),
        can_read=svc.can_read(p, kb),
    )


def _doc(d: Document) -> DocumentOut:
    return DocumentOut(
        id=d.id,
        kb_id=d.kb_id,
        filename=d.filename,
        content_type=d.content_type,
        size_bytes=d.size_bytes,
        status=d.status.value,
        error=d.error,
        title=d.title,
        chunk_count=d.chunk_count,
        created_by=d.created_by,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


@router.get("/bases", response_model=list[KbOut])
async def list_kbs(p: AnyUser, c: ContainerDep) -> list[KbOut]:
    return [_kb(c, p, kb) for kb in await c.knowledge.list_kbs(p)]


@router.post("/bases", response_model=KbOut, status_code=status.HTTP_201_CREATED)
async def create_kb(body: CreateKbIn, p: AnyUser, c: ContainerDep) -> KbOut:
    kb = await c.knowledge.create_kb(
        p,
        name=body.name,
        description=body.description,
        scope=body.scope,
        data_classification=body.data_classification,
    )
    return _kb(c, p, kb)


@router.get("/bases/{kb_id}", response_model=KbOut)
async def get_kb(kb_id: UUID, p: AnyUser, c: ContainerDep) -> KbOut:
    return _kb(c, p, await c.knowledge.get_kb(p, kb_id))


@router.patch("/bases/{kb_id}", response_model=KbOut)
async def update_kb(kb_id: UUID, body: UpdateKbIn, p: AnyUser, c: ContainerDep) -> KbOut:
    kb = await c.knowledge.update_kb(p, kb_id, name=body.name, description=body.description)
    return _kb(c, p, kb)


@router.delete("/bases/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb(kb_id: UUID, p: AnyUser, c: ContainerDep) -> Response:
    await c.knowledge.delete_kb(p, kb_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/bases/{kb_id}/documents", response_model=list[DocumentOut])
async def list_documents(kb_id: UUID, p: AnyUser, c: ContainerDep) -> list[DocumentOut]:
    return [_doc(d) for d in await c.knowledge.list_documents(p, kb_id)]


@router.post(
    "/bases/{kb_id}/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    kb_id: UUID,
    p: AnyUser,
    c: ContainerDep,
    file: Annotated[UploadFile, File()],
) -> DocumentOut:
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValidationError("Tệp quá 25 MB.")
    doc = await c.knowledge.upload(p, kb_id, filename=file.filename or "tai-lieu", data=data)
    return _doc(doc)


@router.post("/documents/{doc_id}/reingest", response_model=DocumentOut)
async def reingest(doc_id: UUID, p: AnyUser, c: ContainerDep) -> DocumentOut:
    return _doc(await c.knowledge.reingest(p, doc_id))


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: UUID, p: AnyUser, c: ContainerDep) -> Response:
    await c.knowledge.delete_document(p, doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/bases/{kb_id}/search", response_model=SearchOut)
async def search(kb_id: UUID, body: SearchIn, p: AnyUser, c: ContainerDep) -> SearchOut:
    """Thử tìm kiếm trong một KB (trang quản lý) — cùng đường đi với tool `kb.search`."""
    await c.knowledge.get_kb(p, kb_id)
    hits = await c.knowledge.search(p, [kb_id], body.query, top_k=body.top_k)
    text, citations = format_hits(hits)
    return SearchOut(text=text, citations=citations, scores=[round(h.score, 4) for h in hits])
