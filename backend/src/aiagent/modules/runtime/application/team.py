"""Master agent (ADR-0017): mỗi agent thành viên là một tool `delegate__<alias>`.

- Mỗi lần giao việc = một run con chạy bằng DANH TÍNH NGƯỜI DÙNG (không leo thang quyền),
  độ sâu tối đa 1 (thành viên không phải supervisor).
- Run con nhiễm (đọc web/DB) → kết quả bị bọc nhãn dữ liệu bên ngoài, Master cũng nhiễm.
- Run con dừng chờ duyệt → Master dừng theo; khi tiếp tục, run con chạy tiếp trước.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.conversations.domain import (
    ConversationRepositoryPort,
    DelegationStatus,
)
from aiagent.modules.identity.domain import Principal
from aiagent.modules.runtime.application.tool_gateway import wrap_untrusted
from aiagent.modules.runtime.domain import (
    ContentBlock,
    Effect,
    RunEvent,
    RunEventType,
    ToolDecision,
    ToolDefinition,
)
from aiagent.shared_kernel.errors import AppError

logger = logging.getLogger(__name__)

DELEGATE_PREFIX = "delegate__"
MAX_RESULT_CHARS = 12_000
_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4000,
            "description": "Việc cần làm, viết đầy đủ và tự chứa (agent kia không thấy hội thoại).",
        },
        "context": {
            "type": "string",
            "maxLength": 8000,
            "description": "Thông tin bổ sung cần thiết (kết quả trước đó…), có thể bỏ trống.",
        },
    },
    "required": ["task"],
    "additionalProperties": False,
}


def alias_of(name: str, taken: set[str]) -> str:
    """Tên agent → alias ASCII cho tên tool (`Tra cứu đơn hàng` → `tra_cuu_don_hang`)."""
    plain = unicodedata.normalize("NFKD", name.replace("đ", "d").replace("Đ", "D"))
    plain = "".join(c for c in plain if not unicodedata.combining(c)).lower()
    base = re.sub(r"[^a-z0-9]+", "_", plain).strip("_")[:40] or "agent"
    alias, n = base, 2
    while alias in taken:
        alias = f"{base[:37]}_{n}"
        n += 1
    taken.add(alias)
    return alias


@dataclass(frozen=True)
class Member:
    agent_id: UUID
    name: str
    alias: str
    when_to_use: str


@dataclass
class ChildOutcome:
    thread_id: UUID
    run_id: UUID
    status: str  # completed | failed | refused | awaiting_approval
    text: str
    error: str | None
    tainted: bool
    requests: list[dict[str, Any]] = field(default_factory=list)


class ChildRunnerPort(Protocol):
    async def run_child(
        self,
        principal: Principal,
        agent_id: UUID,
        task: str,
        *,
        on_event: Callable[[RunEvent], None],
        resume_thread_id: UUID | None = None,
    ) -> ChildOutcome: ...


@dataclass
class TeamTools:
    """Bộ tool của agent supervisor. Hiện thực `ToolExecutorPort`."""

    principal: Principal
    parent_thread_id: UUID
    parent_run_id: UUID
    repo: ConversationRepositoryPort
    audit: AuditService
    runner: ChildRunnerPort
    members: dict[str, Member]  # tên tool → thành viên
    emit: Callable[[RunEvent], None]
    max_delegations: int = 8
    max_parallel: int = 3
    hidden: list[str] = field(default_factory=list)
    tainted: bool = False
    calls: list[str] = field(default_factory=list)
    _sem: asyncio.Semaphore | None = None

    @property
    def definitions(self) -> list[ToolDefinition]:
        out = []
        for name, m in sorted(self.members.items()):
            hint = m.when_to_use or "agent chuyên trách"
            out.append(
                ToolDefinition(
                    name=name,
                    description=f"Giao việc cho agent “{m.name}”. Dùng khi: {hint}",
                    input_schema=_INPUT_SCHEMA,
                )
            )
        return out

    def decide(self, tool_use: ContentBlock) -> ToolDecision:
        # Giao việc chỉ là "đọc" ở tầng Master; thao tác rủi ro bị policy của run con chặn/duyệt.
        return ToolDecision(Effect.ALLOW, str(tool_use.get("name")), "read", "giao việc")

    # ------------------------------------------------------------------ execute
    async def execute(
        self, tool_use: ContentBlock, *, approved_by: str | None = None
    ) -> tuple[ContentBlock, dict[str, object]]:
        name = str(tool_use.get("name", ""))
        tool_use_id = str(tool_use.get("id", ""))
        member = self.members.get(name)
        args = tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {}
        task = str((args or {}).get("task", "")).strip()
        context = str((args or {}).get("context", "")).strip()
        if member is None:
            return self._error(tool_use_id, name, f"Không có agent '{name}' trong nhóm.")
        if not task or len(task) > 4000 or len(context) > 8000:
            return self._error(tool_use_id, name, "Thiếu 'task' hoặc nội dung quá dài.")
        if len(self.calls) >= self.max_delegations:
            return self._error(
                tool_use_id, name, f"Đã giao quá số lần cho phép ({self.max_delegations})."
            )
        self.calls.append(name)
        message = task if not context else f"{task}\n\nThông tin bổ sung:\n{context}"
        delegation = await self.repo.create_delegation(
            self.principal.tenant_id,
            self.parent_thread_id,
            self.parent_run_id,
            tool_use_id,
            member.agent_id,
            message,
        )
        self.emit(
            RunEvent(
                RunEventType.DELEGATION_START,
                {
                    "delegation_id": str(delegation.id),
                    "tool_use_id": tool_use_id,
                    "agent_id": str(member.agent_id),
                    "agent_name": member.name,
                    "task": task[:500],
                },
            )
        )
        await self._audit("delegation.started", member, delegation.id, {})
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.max_parallel)
        async with self._sem:
            return await self._run(tool_use_id, name, member, delegation.id, message, None)

    async def resume(self, tool_use: ContentBlock) -> tuple[ContentBlock, dict[str, object]]:
        """Tiếp tục một lần giao việc sau khi run con đã được duyệt."""
        name = str(tool_use.get("name", ""))
        tool_use_id = str(tool_use.get("id", ""))
        member = self.members.get(name)
        found = await self.repo.delegations_for_tool_uses(
            self.principal.tenant_id, self.parent_thread_id, [tool_use_id]
        )
        if not found or member is None:
            return self._error(tool_use_id, name, "Lần giao việc này không còn hiệu lực.")
        d = found[0]
        if d.status is DelegationStatus.AWAITING_APPROVAL and d.child_thread_id is not None:
            self.emit(
                RunEvent(
                    RunEventType.DELEGATION_START,
                    {
                        "delegation_id": str(d.id),
                        "tool_use_id": tool_use_id,
                        "agent_id": str(member.agent_id),
                        "agent_name": member.name,
                        "task": d.task[:500],
                        "resumed": True,
                    },
                )
            )
            return await self._run(tool_use_id, name, member, d.id, d.task, d.child_thread_id)
        # Đã xong ở lần trước (cùng lượt với một việc khác phải chờ duyệt): dùng lại kết quả.
        outcome = ChildOutcome(
            thread_id=d.child_thread_id or d.parent_thread_id,
            run_id=d.child_run_id or d.parent_run_id,
            status="completed" if d.status is DelegationStatus.COMPLETED else "failed",
            text=d.result or "",
            error=None if d.status is DelegationStatus.COMPLETED else (d.result or "Thất bại"),
            tainted=d.tainted,
        )
        return self._result(tool_use_id, name, member, d.id, outcome)

    # ------------------------------------------------------------------ nội bộ
    async def _run(
        self,
        tool_use_id: str,
        name: str,
        member: Member,
        delegation_id: UUID,
        message: str,
        resume_thread_id: UUID | None,
    ) -> tuple[ContentBlock, dict[str, object]]:
        def forward(ev: RunEvent) -> None:
            data = ev.data
            self.emit(
                RunEvent(
                    RunEventType.DELEGATION_PROGRESS,
                    {
                        "delegation_id": str(delegation_id),
                        "event": ev.type.value,
                        "id": data.get("id"),
                        "name": data.get("name"),
                        "is_error": data.get("is_error"),
                    },
                )
            )

        try:
            outcome = await self.runner.run_child(
                self.principal,
                member.agent_id,
                message,
                on_event=forward,
                resume_thread_id=resume_thread_id,
            )
        except AppError as exc:
            outcome = None
            error = exc.message
        except Exception:
            logger.exception("Giao việc cho agent %s lỗi", member.agent_id)
            outcome = None
            error = "Agent được giao gặp lỗi nội bộ."
        if outcome is None:
            await self.repo.update_delegation(
                self.principal.tenant_id,
                delegation_id,
                status=DelegationStatus.FAILED,
                child_thread_id=resume_thread_id,
                child_run_id=None,
                result=error,
                tainted=False,
            )
            self._end(delegation_id, "failed", error)
            await self._audit("delegation.finished", member, delegation_id, {"status": "failed"})
            return self._error(tool_use_id, name, f"Agent “{member.name}” lỗi: {error}")

        status = {
            "completed": DelegationStatus.COMPLETED,
            "awaiting_approval": DelegationStatus.AWAITING_APPROVAL,
        }.get(outcome.status, DelegationStatus.FAILED)
        stored = outcome.text if status is not DelegationStatus.FAILED else outcome.error
        await self.repo.update_delegation(
            self.principal.tenant_id,
            delegation_id,
            status=status,
            child_thread_id=outcome.thread_id,
            child_run_id=outcome.run_id,
            result=(stored or "")[:MAX_RESULT_CHARS],
            tainted=outcome.tainted,
        )
        await self._audit("delegation.finished", member, delegation_id, {"status": outcome.status})
        if status is DelegationStatus.AWAITING_APPROVAL:
            self._end(delegation_id, "awaiting_approval", "Chờ duyệt thao tác")
            block: ContentBlock = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": "Đang chờ duyệt.",
            }
            return block, {
                "name": name,
                "is_error": False,
                "awaiting_approval": True,
                "requests": [
                    {**r, "delegation_id": str(delegation_id), "agent_name": member.name}
                    for r in outcome.requests
                ],
            }
        return self._result(tool_use_id, name, member, delegation_id, outcome)

    def _result(
        self,
        tool_use_id: str,
        name: str,
        member: Member,
        delegation_id: UUID,
        outcome: ChildOutcome,
    ) -> tuple[ContentBlock, dict[str, object]]:
        ok = outcome.status == "completed"
        body = (outcome.text if ok else (outcome.error or outcome.text)) or "(không có nội dung)"
        if len(body) > MAX_RESULT_CHARS:
            body = body[:MAX_RESULT_CHARS] + "\n…[đã cắt bớt]"
        if outcome.tainted:
            self.tainted = True
            body = wrap_untrusted(f"agent {member.name}", body)
        content = f"Kết quả từ agent “{member.name}”:\n{body}"
        self._end(delegation_id, "completed" if ok else "failed", body[:300])
        block: ContentBlock = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if not ok:
            block["is_error"] = True
        return block, {
            "name": name,
            "is_error": not ok,
            "preview": content[:500],
            "delegation_id": str(delegation_id),
        }

    def _end(self, delegation_id: UUID, status: str, summary: str | None) -> None:
        self.emit(
            RunEvent(
                RunEventType.DELEGATION_END,
                {
                    "delegation_id": str(delegation_id),
                    "status": status,
                    "summary": (summary or "")[:300],
                },
            )
        )

    @staticmethod
    def _error(tool_use_id: str, name: str, message: str) -> tuple[ContentBlock, dict[str, object]]:
        block: ContentBlock = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": message,
            "is_error": True,
        }
        return block, {"name": name, "is_error": True, "preview": message}

    async def _audit(
        self, action: str, member: Member, delegation_id: UUID, extra: dict[str, Any]
    ) -> None:
        await self.audit.record_safely(
            tenant_id=self.principal.tenant_id,
            actor_id=self.principal.subject,
            action=action,
            outcome=Outcome.SUCCESS,
            resource_type="agent",
            resource_id=str(member.agent_id),
            details={
                "delegation_id": str(delegation_id),
                "parent_run_id": str(self.parent_run_id),
                **extra,
            },
        )
