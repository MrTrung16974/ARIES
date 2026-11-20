"""Duyệt thao tác rủi ro (HITL, ADR-0016): liệt kê và quyết định Approval."""

from __future__ import annotations

from uuid import UUID

from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.conversations.domain import (
    Approval,
    ApprovalStatus,
    ConversationRepositoryPort,
)
from aiagent.modules.identity.domain import Principal, Role
from aiagent.modules.runtime.application.policy import can_decide
from aiagent.shared_kernel.errors import ConflictError, ForbiddenError, NotFoundError


class ApprovalService:
    def __init__(self, repo: ConversationRepositoryPort, audit: AuditService) -> None:
        self._repo = repo
        self._audit = audit

    @staticmethod
    def _sees_all(principal: Principal) -> bool:
        return principal.has_any_role(Role.ADMIN)

    def can_decide(self, principal: Principal, approval: Approval) -> bool:
        return can_decide(
            [r.value for r in principal.roles],
            approver_roles=approval.approver_roles,
            is_requester=approval.requester_id == principal.subject,
        )

    async def list(
        self,
        principal: Principal,
        *,
        status: ApprovalStatus | None,
        thread_id: UUID | None = None,
        limit: int = 100,
    ) -> list[Approval]:
        """Người dùng thường thấy yêu cầu của mình; Admin/Owner thấy mọi yêu cầu trong tenant."""
        requester = None if self._sees_all(principal) else principal.subject
        return await self._repo.list_approvals(
            principal.tenant_id,
            status=status,
            requester_id=requester,
            thread_id=thread_id,
            limit=limit,
        )

    async def decide(
        self, principal: Principal, approval_id: UUID, *, approve: bool, note: str | None
    ) -> Approval:
        approval = await self._repo.get_approval(principal.tenant_id, approval_id)
        visible = approval is not None and (
            self._sees_all(principal) or approval.requester_id == principal.subject
        )
        if approval is None or not visible:
            raise NotFoundError("Không tìm thấy yêu cầu duyệt.")
        if not self.can_decide(principal, approval):
            raise ForbiddenError("Bạn không có quyền duyệt thao tác này.")
        status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        decided = await self._repo.decide_approval(
            principal.tenant_id, approval_id, status, principal.subject, note
        )
        if decided is None:
            raise ConflictError("Yêu cầu đã được quyết định hoặc hết hiệu lực.")
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action="approval.decided",
            outcome=Outcome.SUCCESS,
            resource_type="approval",
            resource_id=str(approval_id),
            details={
                "decision": status.value,
                "tool": approval.tool,
                "risk": approval.risk,
                "run_id": str(approval.run_id),
                "requester_id": approval.requester_id,
                "connection_id": approval.connection_id,
            },
        )
        return decided


__all__ = ["ApprovalService"]
