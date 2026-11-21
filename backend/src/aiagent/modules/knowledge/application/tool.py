"""Tool `kb.search` cho agent (ADR-0019): một adapter Plugin tạo theo từng run, đã gắn sẵn người
chạy và danh sách KB họ ĐỌC ĐƯỢC. Kết quả là dữ liệu không tin cậy (taint) + trích dẫn [n]."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from aiagent.modules.identity.domain import Principal
from aiagent.modules.knowledge.application.service import KnowledgeService
from aiagent.modules.knowledge.domain import ChunkHit, KnowledgeBase
from aiagent.plugin_sdk import (
    PLUGIN_API_VERSION,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from aiagent.shared_kernel.errors import AppError

TOOL_NAME = "kb.search"
MAX_SNIPPET = 1500

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 2,
            "maxLength": 500,
            "description": "Câu hỏi hoặc từ khóa cần tìm trong tài liệu (viết đầy đủ, rõ nghĩa).",
        },
        "top_k": {"type": "integer", "minimum": 1, "maximum": 8},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def format_hits(hits: list[ChunkHit]) -> tuple[str, list[dict[str, Any]]]:
    if not hits:
        return "Không tìm thấy đoạn tài liệu nào liên quan.", []
    blocks, citations = [], []
    for n, h in enumerate(hits, start=1):
        where = " · ".join(x for x in (f"trang {h.page}" if h.page else "", h.heading) if x)
        source = f"{h.document_title}" + (f" · {where}" if where else "")
        snippet = h.text if len(h.text) <= MAX_SNIPPET else h.text[:MAX_SNIPPET] + "…"
        blocks.append(f"[{n}] {source}\n{snippet}")
        citations.append(
            {
                "n": n,
                "title": h.document_title,
                "filename": h.filename,
                "page": h.page,
                "heading": h.heading,
                "document_id": str(h.document_id),
                "kb_id": str(h.kb_id),
            }
        )
    guide = "Trả lời dựa trên các đoạn sau và trích dẫn bằng [số] tương ứng:\n\n"
    return guide + "\n\n---\n\n".join(blocks), citations


class KnowledgePlugin(Plugin):
    def __init__(
        self, service: KnowledgeService, principal: Principal, kbs: list[KnowledgeBase]
    ) -> None:
        self._service = service
        self._principal = principal
        self._kb_ids: list[UUID] = [kb.id for kb in kbs]
        names = ", ".join(kb.name for kb in kbs)[:300]
        self._manifest = PluginManifest(
            id="knowledge",
            kind=PluginKind.SKILL,
            name="Kho tri thức",
            version="1.0.0",
            api_version=PLUGIN_API_VERSION,
            tools=(
                ToolSpec(
                    name=TOOL_NAME,
                    description=(
                        f"Tìm trong tài liệu nội bộ ({names}). Dùng khi câu hỏi liên quan quy định,"
                        " chính sách, quy trình, hợp đồng… Trả về các đoạn kèm số trích dẫn [n]."
                    ),
                    risk=RiskLevel.READ,
                    untrusted_output=True,
                    input_schema=_SCHEMA,
                ),
            ),
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if name != TOOL_NAME:
            return ToolResult(f"Không có tool '{name}'.", is_error=True)
        try:
            hits = await self._service.search(
                self._principal,
                self._kb_ids,
                str(arguments.get("query", "")),
                top_k=int(arguments.get("top_k") or 5),
            )
        except AppError as exc:
            return ToolResult(exc.message, is_error=True)
        content, citations = format_hits(hits)
        return ToolResult(content, data={"citations": citations})


class KnowledgeAccess:
    """`KnowledgeAccessPort` cho runtime: tạo adapter theo người chạy."""

    def __init__(self, service: KnowledgeService) -> None:
        self._service = service

    async def plugin_for(
        self, principal: Principal, kb_ids: list[str]
    ) -> tuple[Plugin | None, list[str]]:
        kbs = await self._service.readable(principal, kb_ids)
        found = {str(kb.id) for kb in kbs}
        hidden = [f"kb:{k}" for k in kb_ids if k not in found]
        return (KnowledgePlugin(self._service, principal, kbs) if kbs else None), hidden

    async def classification_of(self, principal: Principal, kb_id: str) -> str | None:
        """Phân loại của KB nếu người này đọc được (kiểm khi lưu agent)."""
        kbs = await self._service.readable(principal, [kb_id])
        return kbs[0].data_classification if kbs else None


__all__ = ["TOOL_NAME", "KnowledgeAccess", "KnowledgePlugin", "format_hits"]
