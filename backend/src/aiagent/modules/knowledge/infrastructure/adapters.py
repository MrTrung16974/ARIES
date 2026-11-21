"""Adapter hạ tầng cho knowledge (ADR-0019): TEI (embed/rerank), kho tệp, hàng đợi arq."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from aiagent.modules.knowledge.domain import EMBEDDING_DIM
from aiagent.shared_kernel.errors import AppError

logger = logging.getLogger(__name__)

_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+){2}$")


class KnowledgeBackendError(AppError):
    code = "knowledge_backend"
    status_code = 503
    public_message = "Dịch vụ tìm kiếm tri thức chưa sẵn sàng."


class TeiEmbedder:
    """Text Embeddings Inference — `POST /embed` (bge-m3, vector đã chuẩn hóa)."""

    def __init__(self, url: str, http: httpx.AsyncClient, timeout_s: float = 120.0) -> None:
        self._url = url.rstrip("/")
        self._http = http
        self._timeout = timeout_s

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            r = await self._http.post(
                f"{self._url}/embed",
                json={"inputs": texts, "normalize": True, "truncate": True},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning("Embedder không phản hồi: %s", type(exc).__name__)
            raise KnowledgeBackendError() from exc
        if r.status_code != 200:
            logger.error("Embedder lỗi HTTP %s", r.status_code)
            raise KnowledgeBackendError()
        vectors: list[list[float]] = r.json()
        if len(vectors) != len(texts) or any(len(v) != EMBEDDING_DIM for v in vectors):
            raise KnowledgeBackendError("Embedder trả về vector sai kích thước.")
        return vectors


class TeiReranker:
    """Text Embeddings Inference — `POST /rerank` (bge-reranker-v2-m3)."""

    def __init__(self, url: str, http: httpx.AsyncClient, timeout_s: float = 60.0) -> None:
        self._url = url.rstrip("/")
        self._http = http
        self._timeout = timeout_s

    async def rerank(self, query: str, texts: list[str]) -> list[float]:
        try:
            r = await self._http.post(
                f"{self._url}/rerank",
                json={"query": query, "texts": texts, "truncate": True, "raw_scores": False},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise KnowledgeBackendError() from exc
        if r.status_code != 200:
            raise KnowledgeBackendError()
        scores = [0.0] * len(texts)
        for item in r.json():
            scores[int(item["index"])] = float(item["score"])
        return scores


class FileBlobStore:
    """Tệp gốc trên volume (ADR-0019). Khóa cố định dạng `tenant/kb/doc` — chặn path traversal."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, key: str) -> Path:
        if not _KEY_RE.match(key):
            raise ValueError("Khóa tệp không hợp lệ")
        return self._root / key

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        await asyncio.to_thread(write)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise AppError("Không tìm thấy tệp gốc của tài liệu.") from exc

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)


class ArqIngestQueue:
    """Đẩy job `ingest_document` cho worker arq."""

    def __init__(self, redis_dsn: str) -> None:
        self._dsn = redis_dsn
        self._pool: Any = None

    async def enqueue_ingest(self, tenant_id: str, doc_id: UUID) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        if self._pool is None:
            self._pool = await create_pool(RedisSettings.from_dsn(self._dsn))
        await self._pool.enqueue_job("ingest_document", tenant_id, str(doc_id))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
