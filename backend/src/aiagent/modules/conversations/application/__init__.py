from __future__ import annotations

from uuid import UUID

from aiagent.modules.conversations.domain import (
    ConversationRepositoryPort,
    Feedback,
    Message,
    Thread,
)
from aiagent.modules.identity.domain import Principal
from aiagent.shared_kernel.errors import NotFoundError, ValidationError


class ConversationService:
    """Thread thuộc về người tạo; người khác (kể cả admin) không đọc được qua API này."""

    def __init__(self, repo: ConversationRepositoryPort) -> None:
        self.repo = repo

    async def owned_thread(self, principal: Principal, thread_id: UUID) -> Thread:
        thread = await self.repo.get_thread(principal.tenant_id, thread_id)
        if thread is None or thread.user_id != principal.subject:
            raise NotFoundError("Không tìm thấy hội thoại.")
        return thread

    async def threads(
        self, principal: Principal, agent_id: UUID | None, limit: int = 50
    ) -> list[Thread]:
        return await self.repo.list_threads(principal.tenant_id, principal.subject, agent_id, limit)

    async def messages(self, principal: Principal, thread_id: UUID) -> list[Message]:
        await self.owned_thread(principal, thread_id)
        return await self.repo.list_messages(principal.tenant_id, thread_id)

    async def give_feedback(
        self,
        principal: Principal,
        thread_id: UUID,
        message_id: int,
        *,
        rating: int,
        comment: str = "",
    ) -> Feedback:
        """Đánh giá 👍/👎 câu trả lời của agent trong hội thoại của chính mình (ADR-0020)."""
        thread = await self.owned_thread(principal, thread_id)
        if rating not in (1, -1):
            raise ValidationError("Đánh giá phải là 1 hoặc -1.")
        messages = await self.repo.list_messages(principal.tenant_id, thread_id)
        if not any(m.id == message_id and m.role == "assistant" for m in messages):
            raise NotFoundError("Không tìm thấy câu trả lời.")
        return await self.repo.upsert_feedback(
            principal.tenant_id,
            thread_id,
            message_id,
            thread.agent_id,
            principal.subject,
            rating,
            comment.strip()[:1000],
        )


__all__ = ["ConversationService"]
