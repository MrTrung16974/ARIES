"""Use case vận hành (ADR-0020).

Quyền: admin/auditor xem toàn tenant; builder chỉ xem agent mình sở hữu (phải lọc theo agent).
Mở toàn văn một hội thoại của người khác là truy cập dữ liệu nhạy cảm → ghi audit
`ops.conversation_viewed` (kể cả khi người xem là admin).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from aiagent.modules.agents.application import AgentService
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.conversations.domain import ConversationRepositoryPort, Feedback, Thread
from aiagent.modules.identity.domain import Principal, Role
from aiagent.modules.ops.domain import (
    CHANNELS,
    FeedbackItem,
    OpsFilter,
    OpsQueryPort,
    ThreadSummary,
    UsageRow,
    channel_of,
)
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import ForbiddenError, NotFoundError, ValidationError

_CLIP = 2000
MAX_RANGE = timedelta(days=366)


@dataclass(frozen=True)
class TranscriptMessage:
    id: int
    role: str
    created_at: datetime
    parts: list[dict[str, Any]]


@dataclass(frozen=True)
class Transcript:
    thread: Thread
    channel: str
    messages: list[TranscriptMessage]
    feedback: list[Feedback]


@dataclass(frozen=True)
class UsageReport:
    since: datetime
    until: datetime
    rows: list[UsageRow]
    runs: int
    failed_runs: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


def _clip(value: str) -> str:
    return value if len(value) <= _CLIP else value[: _CLIP - 1] + "…"


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def transcript_parts(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rút gọn content block để hiển thị: bỏ thinking (kèm chữ ký), cắt tool input/result dài."""
    parts: list[dict[str, Any]] = []
    for b in content:
        kind = b.get("type")
        if kind == "text":
            parts.append({"type": "text", "text": b.get("text", "")})
        elif kind == "tool_use":
            raw = json.dumps(b.get("input", {}), ensure_ascii=False)
            parts.append(
                {"type": "tool_use", "id": b.get("id"), "name": b.get("name"), "input": _clip(raw)}
            )
        elif kind == "tool_result":
            parts.append(
                {
                    "type": "tool_result",
                    "tool_use_id": b.get("tool_use_id"),
                    "is_error": bool(b.get("is_error")),
                    "text": _clip(_result_text(b.get("content"))),
                }
            )
    return parts


class OpsService:
    def __init__(
        self,
        query: OpsQueryPort,
        conversations: ConversationRepositoryPort,
        agents: AgentService,
        audit: AuditService,
    ) -> None:
        self._query = query
        self._conversations = conversations
        self._agents = agents
        self._audit = audit

    # ------------------------------------------------------------------ quyền
    async def _authorize(self, principal: Principal, agent_id: UUID | None) -> None:
        if principal.has_any_role(Role.ADMIN, Role.AUDITOR):
            return
        if agent_id is None:
            raise ForbiddenError("Chỉ admin/auditor xem được toàn bộ; hãy chọn agent của bạn.")
        agent = await self._agents.get(principal, agent_id)
        if not self._agents.can_edit(principal, agent):
            raise ForbiddenError("Bạn không có quyền xem nhật ký agent này.")

    @staticmethod
    def _check(f: OpsFilter) -> None:
        if f.channel is not None and f.channel not in CHANNELS:
            raise ValidationError(f"Kênh không hợp lệ (chọn: {', '.join(CHANNELS)}).")
        if f.rating not in (None, 1, -1):
            raise ValidationError("Đánh giá phải là 1 hoặc -1.")
        if f.since and f.until and f.since >= f.until:
            raise ValidationError("Khoảng thời gian không hợp lệ.")

    # ------------------------------------------------------------------ truy vấn
    async def threads(
        self, principal: Principal, f: OpsFilter, *, limit: int = 50, before: datetime | None = None
    ) -> list[ThreadSummary]:
        self._check(f)
        await self._authorize(principal, f.agent_id)
        return await self._query.threads(principal.tenant_id, f, limit, before)

    async def feedback(
        self, principal: Principal, f: OpsFilter, *, limit: int = 100
    ) -> list[FeedbackItem]:
        self._check(f)
        await self._authorize(principal, f.agent_id)
        return await self._query.feedback(principal.tenant_id, f, limit)

    async def usage(self, principal: Principal, f: OpsFilter) -> UsageReport:
        until = f.until or utcnow()
        since = f.since or until - timedelta(days=30)
        if until - since > MAX_RANGE:
            raise ValidationError("Khoảng thời gian tối đa 366 ngày.")
        f = OpsFilter(agent_id=f.agent_id, channel=f.channel, since=since, until=until)
        self._check(f)
        await self._authorize(principal, f.agent_id)
        rows = await self._query.usage(principal.tenant_id, f)
        return UsageReport(
            since=since,
            until=until,
            rows=rows,
            runs=sum(r.runs for r in rows),
            failed_runs=sum(r.failed_runs for r in rows),
            input_tokens=sum(r.input_tokens for r in rows),
            output_tokens=sum(r.output_tokens for r in rows),
            cost_usd=round(sum(r.cost_usd for r in rows), 6),
        )

    async def transcript(self, principal: Principal, thread_id: UUID) -> Transcript:
        thread = await self._conversations.get_thread(principal.tenant_id, thread_id)
        if thread is None:
            raise NotFoundError("Không tìm thấy hội thoại.")
        try:
            await self._authorize(principal, thread.agent_id)
        except (NotFoundError, ForbiddenError):  # không lộ sự tồn tại của thread
            raise NotFoundError("Không tìm thấy hội thoại.") from None
        messages = await self._conversations.list_messages(principal.tenant_id, thread_id)
        feedback = await self._conversations.feedback_for_thread(principal.tenant_id, thread_id)
        if thread.user_id != principal.subject:
            await self._audit.record_safely(
                tenant_id=principal.tenant_id,
                actor_id=principal.subject,
                action="ops.conversation_viewed",
                outcome=Outcome.SUCCESS,
                resource_type="thread",
                resource_id=str(thread_id),
                details={"agent_id": str(thread.agent_id), "channel": channel_of(thread.user_id)},
            )
        return Transcript(
            thread=thread,
            channel=channel_of(thread.user_id),
            messages=[
                TranscriptMessage(m.id, m.role, m.created_at, transcript_parts(m.content))
                for m in messages
            ],
            feedback=feedback,
        )
