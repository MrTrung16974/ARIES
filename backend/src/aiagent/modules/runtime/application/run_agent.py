"""Use case: chat với một agent — chuẩn bị run, chạy vòng lặp, lưu lịch sử, ghi usage/audit.

HITL (ADR-0016): khi vòng lặp dừng ở tool cần duyệt, run kết thúc với trạng thái
`awaiting_approval` và các Approval được tạo. Sau khi có quyết định, `prepare_resume` mở run mới
trên cùng hội thoại: chạy (hoặc từ chối) các tool đang chờ rồi cho model chạy tiếp.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from aiagent.modules.agents.application import AgentService
from aiagent.modules.agents.domain import (
    Agent,
    AgentSpec,
    AgentStatus,
    AgentVersion,
    DataClassification,
    Team,
    render_system_prompt,
)
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.conversations.application import ConversationService
from aiagent.modules.conversations.domain import (
    Approval,
    ApprovalStatus,
    DelegationStatus,
    Run,
    RunStatus,
    Thread,
)
from aiagent.modules.identity.domain import Principal
from aiagent.modules.runtime.application.router import ModelRouter
from aiagent.modules.runtime.application.team import (
    DELEGATE_PREFIX,
    ChildOutcome,
    Member,
    TeamTools,
    alias_of,
)
from aiagent.modules.runtime.application.tool_gateway import (
    BoundTools,
    ToolGateway,
    history_is_tainted,
)
from aiagent.modules.runtime.domain import (
    AgentLoopPort,
    ChatMessage,
    ChatModelPort,
    ContentBlock,
    Effect,
    LoopInput,
    ModelRequest,
    RunEvent,
    RunEventType,
    estimate_cost,
    history_for_model,
    pending_tool_uses,
    text_of,
)
from aiagent.shared_kernel.errors import AppError, ConflictError
from aiagent.shared_kernel.usage import Usage

logger = logging.getLogger(__name__)

MAX_USER_MESSAGE_CHARS = 20_000
_OK_STATUSES = (RunStatus.COMPLETED, RunStatus.AWAITING_APPROVAL)


@dataclass(frozen=True)
class EphemeralResult:
    """Kết quả chạy thử một spec (eval của Architect) — không lưu lịch sử."""

    text: str
    tools_used: list[str]
    status: str
    error: str | None
    usage: Usage


@dataclass
class PreparedRun:
    principal: Principal
    agent: Agent
    version: AgentVersion
    thread: Thread
    run: Run
    model: ChatModelPort
    tools: BoundTools | TeamTools
    history: list[ChatMessage]
    user_message: str | None  # None = tiếp tục sau khi duyệt
    resume_tool_uses: list[ContentBlock] | None = None
    # Sự kiện từ run con (Master giao việc) — gộp vào luồng SSE của run này.
    sink: asyncio.Queue[object] = field(default_factory=asyncio.Queue)


@dataclass(frozen=True)
class PendingState:
    """Trạng thái chờ duyệt của một hội thoại (gồm cả run con của Master)."""

    waiting: bool
    can_resume: bool
    approvals: list[Approval]


@dataclass(frozen=True)
class _ResumeDone:
    message: ChatMessage
    awaiting: list[dict[str, Any]]


_END = object()


class _Paused(Exception):  # noqa: N818 - tín hiệu điều khiển nội bộ, không phải lỗi
    """Run dừng chờ duyệt ngay trong pha tiếp tục (run con lại cần duyệt)."""


_CLASS_ORDER = list(DataClassification)


class RunAgentService:
    def __init__(
        self,
        agents: AgentService,
        conversations: ConversationService,
        gateway: ToolGateway,
        router: ModelRouter,
        loop: AgentLoopPort,
        audit: AuditService,
    ) -> None:
        self._agents = agents
        self._conv = conversations
        self._gateway = gateway
        self._router = router
        self._loop = loop
        self._audit = audit

    async def _history(self, tenant_id: str, thread_id: UUID) -> list[ChatMessage]:
        stored = await self._conv.repo.list_messages(tenant_id, thread_id)
        return [{"role": m.role, "content": m.content} for m in stored]

    async def _bind(
        self,
        principal: Principal,
        version: AgentVersion,
        agent_id: UUID,
        run: Run,
        history: list[ChatMessage],
        thread: Thread,
        sink: asyncio.Queue[object],
    ) -> BoundTools | TeamTools:
        tools: BoundTools | TeamTools
        if version.spec.graph_type == "supervisor":
            tools = await self._team_tools(principal, version.spec, agent_id, run, thread, sink)
        else:
            tools = await self._gateway.bind(
                version.spec, principal, agent_id=str(agent_id), run_id=str(run.id)
            )
        tools.tainted = history_is_tainted(history)
        return tools

    async def _team_tools(
        self,
        principal: Principal,
        spec: AgentSpec,
        agent_id: UUID,
        run: Run,
        thread: Thread,
        sink: asyncio.Queue[object],
    ) -> TeamTools:
        """Thành viên = agent người chat ĐƯỢC CHẠY (quyền theo người dùng, không theo Master)."""
        team = spec.team or Team()
        limit = _CLASS_ORDER.index(spec.data_classification)
        if team.mode == "fixed":
            candidates = [(m.agent_id, m.when_to_use) for m in team.members]
        else:
            excluded = set(team.exclude)
            candidates = [
                (a.id, "")
                for a in await self._agents.list_agents(principal)
                if a.published_version is not None
                and a.status is not AgentStatus.ARCHIVED
                and a.id != agent_id
                and a.id not in excluded
            ]
        members: dict[str, Member] = {}
        hidden: list[str] = []
        taken: set[str] = set()
        for candidate_id, override in candidates:
            try:
                agent, version = await self._agents.resolve_for_run(
                    principal, candidate_id, draft=False
                )
            except AppError:
                hidden.append(f"agent:{candidate_id}")
                continue
            member_spec = version.spec
            if member_spec.graph_type == "supervisor" or agent.id == agent_id:
                continue  # độ sâu tối đa 1
            if _CLASS_ORDER.index(member_spec.data_classification) > limit:
                hidden.append(f"agent:{candidate_id}")  # dữ liệu mật hơn Master
                continue
            alias = alias_of(agent.name, taken)
            members[DELEGATE_PREFIX + alias] = Member(
                agent_id=agent.id,
                name=agent.name,
                alias=alias,
                when_to_use=override or member_spec.when_to_use or member_spec.description,
            )
        return TeamTools(
            principal=principal,
            parent_thread_id=thread.id,
            parent_run_id=run.id,
            repo=self._conv.repo,
            audit=self._audit,
            runner=self,
            members=members,
            emit=sink.put_nowait,
            max_delegations=team.max_delegations_per_run,
            max_parallel=team.max_parallel,
            hidden=hidden,
        )

    async def prepare(
        self,
        principal: Principal,
        agent_id: UUID,
        message: str,
        *,
        thread_id: UUID | None,
        draft: bool,
    ) -> PreparedRun:
        """Mọi kiểm tra có thể thất bại chạy ở đây, trước khi mở stream, để trả HTTP lỗi rõ ràng."""
        message = message.strip()
        if not message or len(message) > MAX_USER_MESSAGE_CHARS:
            raise AppError("Tin nhắn trống hoặc quá dài.")
        agent, version = await self._agents.resolve_for_run(principal, agent_id, draft=draft)
        model = self._router.select(version.spec)

        if thread_id is not None:
            thread = await self._conv.owned_thread(principal, thread_id)
            if thread.agent_id != agent_id:
                raise ConflictError("Hội thoại thuộc agent khác.")
            if thread.agent_version != version.version or thread.is_draft != draft:
                # System prompt/tool phải giữ nguyên trong một hội thoại (prompt cache, thinking).
                raise ConflictError("Agent đã có version mới — hãy mở hội thoại mới.")
        else:
            thread = await self._conv.repo.create_thread(
                principal.tenant_id,
                principal.subject,
                agent_id,
                version.version,
                draft,
                title=message[:80],
            )

        history = await self._history(principal.tenant_id, thread.id)
        run = await self._conv.repo.start_run(
            principal.tenant_id,
            thread.id,
            agent_id,
            version.version,
            principal.subject,
            version.spec.model.model,
        )
        # Tin mới khi còn tool chờ (bị ngắt / chờ duyệt) → yêu cầu duyệt hết hiệu lực, thêm
        # tool_result lỗi (append-only) để lịch sử hợp lệ.
        pending = pending_tool_uses(history)
        if pending:
            await self._conv.repo.expire_pending(principal.tenant_id, thread.id)
            # Master: yêu cầu duyệt nằm ở run con → cũng hết hiệu lực.
            for d in await self._conv.repo.delegations_for_tool_uses(
                principal.tenant_id, thread.id, [str(b.get("id")) for b in pending]
            ):
                if d.child_thread_id is not None:
                    await self._conv.repo.expire_pending(principal.tenant_id, d.child_thread_id)
            repair = [
                {
                    "type": "tool_result",
                    "tool_use_id": b["id"],
                    "is_error": True,
                    "content": "Tool không được thực thi (bị gián đoạn, hoặc yêu cầu duyệt "
                    "đã hết hiệu lực vì người dùng gửi tin nhắn mới).",
                }
                for b in pending
            ]
            await self._conv.repo.append_message(
                principal.tenant_id, thread.id, run.id, "user", repair
            )
            history.append({"role": "user", "content": repair})

        sink: asyncio.Queue[object] = asyncio.Queue()
        tools = await self._bind(principal, version, agent_id, run, history, thread, sink)
        return PreparedRun(
            principal, agent, version, thread, run, model, tools, history, message, None, sink
        )

    async def prepare_resume(self, principal: Principal, thread_id: UUID) -> PreparedRun:
        """Tiếp tục hội thoại đang chờ duyệt — chỉ khi mọi yêu cầu đã có quyết định."""
        thread = await self._conv.owned_thread(principal, thread_id)
        agent, version = await self._agents.resolve_for_run(
            principal, thread.agent_id, draft=thread.is_draft
        )
        if version.version != thread.agent_version:
            raise ConflictError("Agent đã có version mới — hãy mở hội thoại mới.")
        model = self._router.select(version.spec)
        history = await self._history(principal.tenant_id, thread.id)
        pending = pending_tool_uses(history)
        if not pending:
            raise ConflictError("Hội thoại không có thao tác nào đang chờ.")
        state = await self._pending_state(principal, thread.id, history)
        if any(a.status is ApprovalStatus.PENDING for a in state.approvals):
            raise ConflictError("Vẫn còn thao tác chưa được duyệt.")
        run = await self._conv.repo.start_run(
            principal.tenant_id,
            thread.id,
            agent.id,
            version.version,
            principal.subject,
            version.spec.model.model,
        )
        sink: asyncio.Queue[object] = asyncio.Queue()
        tools = await self._bind(principal, version, agent.id, run, history, thread, sink)
        return PreparedRun(
            principal, agent, version, thread, run, model, tools, history, None, pending, sink
        )

    # ------------------------------------------------------------------ chờ duyệt
    async def pending_state(self, principal: Principal, thread_id: UUID) -> PendingState:
        await self._conv.owned_thread(principal, thread_id)
        history = await self._history(principal.tenant_id, thread_id)
        return await self._pending_state(principal, thread_id, history)

    async def _pending_state(
        self, principal: Principal, thread_id: UUID, history: list[ChatMessage]
    ) -> PendingState:
        """Yêu cầu duyệt mới nhất cho các tool đang chờ — kể cả của run con (Master)."""
        tenant = principal.tenant_id
        pending = pending_tool_uses(history)
        if not pending:
            return PendingState(False, False, [])
        ids = [str(t.get("id")) for t in pending]
        found = list(await self._conv.repo.approvals_for_tool_uses(tenant, thread_id, ids))
        waiting_children = 0
        for d in await self._conv.repo.delegations_for_tool_uses(tenant, thread_id, ids):
            if d.status is not DelegationStatus.AWAITING_APPROVAL or d.child_thread_id is None:
                continue
            waiting_children += 1
            child_history = await self._history(tenant, d.child_thread_id)
            child_ids = [str(t.get("id")) for t in pending_tool_uses(child_history)]
            if child_ids:
                found += await self._conv.repo.approvals_for_tool_uses(
                    tenant, d.child_thread_id, child_ids
                )
        latest: dict[tuple[UUID, str], Approval] = {}
        for a in sorted(found, key=lambda x: x.created_at, reverse=True):
            latest.setdefault((a.thread_id, a.tool_use_id), a)
        approvals = list(latest.values())
        decided = all(a.status is not ApprovalStatus.PENDING for a in approvals)
        return PendingState(True, decided and (bool(approvals) or waiting_children > 0), approvals)

    # ------------------------------------------------------------------ run con (Master)
    async def run_child(
        self,
        principal: Principal,
        agent_id: UUID,
        task: str,
        *,
        on_event: Callable[[RunEvent], None],
        resume_thread_id: UUID | None = None,
    ) -> ChildOutcome:
        """Chạy agent thành viên như người dùng tự chat với nó (hội thoại riêng, lưu đầy đủ)."""

        async def go() -> ChildOutcome:
            if resume_thread_id is not None:
                p = await self.prepare_resume(principal, resume_thread_id)
            else:
                p = await self.prepare(principal, agent_id, task, thread_id=None, draft=False)
            if p.version.spec.graph_type == "supervisor":
                raise AppError("Agent điều phối không nhận việc từ agent khác.")
            text, status, error = "", "failed", None
            requests: list[dict[str, Any]] = []
            async for ev in self.stream(p):
                data = ev.data
                if ev.type is RunEventType.TEXT:
                    text += str(data.get("text", ""))
                elif ev.type in (RunEventType.TOOL_START, RunEventType.TOOL_END):
                    if ev.type is RunEventType.TOOL_START:
                        text = ""  # chỉ giữ câu trả lời sau lần gọi tool cuối
                    on_event(ev)
                elif ev.type is RunEventType.APPROVAL_REQUIRED:
                    requests = [r for r in data.get("requests") or [] if isinstance(r, dict)]
                elif ev.type is RunEventType.ERROR:
                    error = str(data.get("message") or "Lỗi")
                elif ev.type is RunEventType.DONE:
                    status = str(data.get("status"))
            return ChildOutcome(
                thread_id=p.thread.id,
                run_id=p.run.id,
                status=status,
                text=text.strip(),
                error=error,
                tainted=p.tools.tainted,
                requests=requests,
            )

        # Context sạch: vòng lặp LangGraph của run con không dính ngữ cảnh graph của Master.
        return await asyncio.create_task(go(), context=contextvars.Context())

    async def _merged(self, p: PreparedRun, source: AsyncIterator[Any]) -> AsyncIterator[Any]:
        """Gộp phần tử của `source` với sự kiện run con (p.sink) theo thứ tự xảy ra."""
        queue = p.sink

        async def pump() -> None:
            try:
                async for item in source:
                    await queue.put(item)
            finally:
                await queue.put(_END)

        task = asyncio.create_task(pump())
        try:
            while True:
                item = await queue.get()
                if item is _END:
                    break
                yield item
            await task  # ném lại lỗi của source
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _resume_results(self, p: PreparedRun) -> AsyncIterator[RunEvent | _ResumeDone]:
        """Chạy các tool đang chờ theo quyết định duyệt; phần tử cuối là `_ResumeDone`."""
        pending = p.resume_tool_uses or []
        if isinstance(p.tools, TeamTools):
            awaiting: list[dict[str, Any]] = []
            team_results: list[ContentBlock] = []
            for tool_use in pending:
                tid = str(tool_use.get("id"))
                yield RunEvent(RunEventType.TOOL_START, {"id": tid, "name": tool_use.get("name")})
                team_block, team_info = await p.tools.resume(tool_use)
                if team_info.get("awaiting_approval"):
                    raw = team_info.get("requests")
                    awaiting += (
                        [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
                    )
                    continue
                team_results.append(team_block)
                yield RunEvent(RunEventType.TOOL_END, {"id": tid, **team_info})
            yield _ResumeDone({"role": "user", "content": team_results}, awaiting)
            return
        approvals = await self._conv.repo.approvals_for_tool_uses(
            p.principal.tenant_id, p.thread.id, [str(t.get("id")) for t in pending]
        )
        latest: dict[str, Approval] = {}
        for a in approvals:  # repo trả mới nhất trước
            latest.setdefault(a.tool_use_id, a)

        results: list[ContentBlock] = []
        for tool_use in pending:
            tid = str(tool_use.get("id"))
            yield RunEvent(RunEventType.TOOL_START, {"id": tid, "name": tool_use.get("name")})
            decision = p.tools.decide(tool_use)
            approval = latest.get(tid)
            approved_by: str | None = None
            refusal: str | None = None
            if decision.effect is Effect.DENY:
                refusal = f"Policy từ chối: {decision.reason}"
            elif decision.effect is Effect.APPROVE:
                if approval is not None and approval.status is ApprovalStatus.APPROVED:
                    approved_by = approval.decided_by or "unknown"
                elif approval is not None and approval.status is ApprovalStatus.DENIED:
                    refusal = "Người duyệt đã TỪ CHỐI thao tác này."
                    if approval.decision_note:
                        refusal += f" Ghi chú: {approval.decision_note}"
                else:
                    refusal = "Thao tác không được duyệt (đã hết hiệu lực)."
            if refusal is not None:
                block: ContentBlock = {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "is_error": True,
                    "content": refusal + " Không tự thử lại; hãy báo cho người dùng.",
                }
                info: dict[str, object] = {
                    "name": decision.tool_name,
                    "is_error": True,
                    "denied": True,
                    "preview": refusal,
                }
            else:
                block, info = await p.tools.execute(tool_use, approved_by=approved_by)
            results.append(block)
            yield RunEvent(RunEventType.TOOL_END, {"id": tid, **info})
        yield _ResumeDone({"role": "user", "content": results}, [])

    async def stream(self, p: PreparedRun) -> AsyncIterator[RunEvent]:
        spec = p.version.spec
        tenant = p.principal.tenant_id
        status: RunStatus = RunStatus.FAILED
        error: str | None = "Run bị ngắt."
        usage = Usage()
        model_name = spec.model.model

        yield RunEvent(
            RunEventType.RUN_STARTED,
            {
                "run_id": str(p.run.id),
                "thread_id": str(p.thread.id),
                "agent_id": str(p.agent.id),
                "agent_version": p.version.version,
                "model": spec.model.model,
                "draft": p.thread.is_draft,
                "hidden_tools": p.tools.hidden,
                "resume": p.user_message is None,
            },
        )
        try:
            new: ChatMessage
            awaiting: list[dict[str, Any]] = []
            if p.user_message is not None:
                new = {"role": "user", "content": [{"type": "text", "text": p.user_message}]}
            else:
                new = {"role": "user", "content": []}
                async for item in self._merged(p, self._resume_results(p)):
                    if isinstance(item, _ResumeDone):
                        new, awaiting = item.message, item.awaiting
                    elif isinstance(item, RunEvent):
                        yield item
            if awaiting:
                # Run con lại dừng chờ duyệt: Master dừng tiếp, chưa ghi tool_result.
                yield RunEvent(RunEventType.APPROVAL_REQUIRED, {"requests": awaiting})
                raise _Paused
            await self._conv.repo.append_message(
                tenant, p.thread.id, p.run.id, "user", new["content"]
            )
            messages = history_for_model([*p.history, new])
            request = ModelRequest(
                model=spec.model.model,
                system=render_system_prompt(spec),
                messages=messages,
                tools=p.tools.definitions,
                effort=spec.model.effort.value,
                max_tokens=spec.model.max_tokens,
            )
            loop = LoopInput(
                model=p.model,
                request=request,
                tools=p.tools,
                max_iterations=spec.limits.max_iterations,
                max_tokens_per_run=spec.limits.max_tokens_per_run,
            )
            async with asyncio.timeout(spec.limits.timeout_s):
                async for item in self._merged(p, self._loop.run(loop)):
                    if not isinstance(item, RunEvent):
                        continue
                    event = item
                    data = event.data
                    if event.type is RunEventType.MESSAGE:
                        content = data.get("content")
                        if isinstance(content, list) and content:
                            await self._conv.repo.append_message(
                                tenant, p.thread.id, p.run.id, str(data["role"]), content
                            )
                        continue
                    if event.type is RunEventType.APPROVAL_REQUIRED:
                        raw = data.get("requests")
                        delegated = isinstance(raw, list) and any(
                            isinstance(r, dict) and r.get("delegation_id") for r in raw
                        )
                        # Yêu cầu của run con đã được tạo ở run con — chỉ chuyển tiếp.
                        yield event if delegated else await self._create_approvals(p, data)
                        continue
                    if event.type is RunEventType.DONE:
                        status = RunStatus(str(data["status"]))
                        err = data.get("error")
                        error = str(err) if err else None
                        loop_usage = data.get("usage")
                        usage = loop_usage if isinstance(loop_usage, Usage) else Usage()
                        model_name = str(data.get("model") or model_name)
                        continue
                    yield event
        except _Paused:
            status, error = RunStatus.AWAITING_APPROVAL, None
        except TimeoutError:
            status, error = RunStatus.FAILED, f"Quá thời gian cho phép ({spec.limits.timeout_s}s)."
        except AppError as exc:
            status, error = RunStatus.FAILED, exc.message
        except Exception:
            logger.exception("Run %s lỗi", p.run.id)
            status, error = RunStatus.FAILED, "Lỗi nội bộ khi chạy agent."
        finally:
            cost = estimate_cost(model_name, usage)
            await asyncio.shield(self._finish(p, status, usage, cost, error))

        if error and status not in _OK_STATUSES:
            yield RunEvent(RunEventType.ERROR, {"message": error, "status": status.value})
        yield RunEvent(
            RunEventType.USAGE, {**usage.as_dict(), "cost_usd": estimate_cost(model_name, usage)}
        )
        yield RunEvent(
            RunEventType.DONE,
            {"status": status.value, "run_id": str(p.run.id), "thread_id": str(p.thread.id)},
        )

    async def _create_approvals(self, p: PreparedRun, data: dict[str, object]) -> RunEvent:
        raw = data.get("requests")
        requests = [dict(r) for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
        created = await self._conv.repo.create_approvals(
            p.principal.tenant_id, p.thread.id, p.run.id, p.agent.id, p.principal.subject, requests
        )
        by_tool = {a.tool_use_id: a for a in created}
        for r in requests:
            a = by_tool.get(str(r.get("tool_use_id")))
            if a is not None:
                r["approval_id"] = str(a.id)
        await self._audit.record_safely(
            tenant_id=p.principal.tenant_id,
            actor_id=p.principal.subject,
            action="approval.requested",
            outcome=Outcome.SUCCESS,
            resource_type="run",
            resource_id=str(p.run.id),
            details={
                "tools": [str(r.get("tool")) for r in requests],
                "approvals": [str(a.id) for a in created],
            },
        )
        return RunEvent(RunEventType.APPROVAL_REQUIRED, {"requests": requests})

    async def _finish(
        self, p: PreparedRun, status: RunStatus, usage: Usage, cost: float, error: str | None
    ) -> None:
        try:
            await self._conv.repo.finish_run(
                p.principal.tenant_id, p.run.id, status, usage, cost, error
            )
        except Exception:
            logger.exception("Không lưu được kết quả run %s", p.run.id)
        await self._audit.record_safely(
            tenant_id=p.principal.tenant_id,
            actor_id=p.principal.subject,
            action="agent.run",
            outcome=Outcome.SUCCESS if status in _OK_STATUSES else Outcome.FAILURE,
            resource_type="agent",
            resource_id=str(p.agent.id),
            details={
                "run_id": str(p.run.id),
                "thread_id": str(p.thread.id),
                "version": p.version.version,
                "status": status.value,
                "resume": p.user_message is None,
                "tokens": usage.total,
                "cost_usd": cost,
            },
        )

    # ------------------------------------------------------------------ sandbox
    async def run_ephemeral(
        self, principal: Principal, spec: AgentSpec, message: str, *, run_label: str
    ) -> EphemeralResult:
        """Chạy một lượt với spec nháp: tool không phải `read` chạy dry-run, không ghi DB.

        Vẫn qua ModelRouter (chặn `restricted`), ToolGateway (validate, audit) như run thật.
        """
        model = self._router.select(spec)
        tools = await self._gateway.bind(
            spec, principal, agent_id="draft", run_id=run_label, dry_run=True
        )
        request = ModelRequest(
            model=spec.model.model,
            system=render_system_prompt(spec),
            messages=[{"role": "user", "content": [{"type": "text", "text": message}]}],
            tools=tools.definitions,
            effort=spec.model.effort.value,
            max_tokens=spec.model.max_tokens,
        )
        loop = LoopInput(
            model=model,
            request=request,
            tools=tools,
            max_iterations=spec.limits.max_iterations,
            max_tokens_per_run=spec.limits.max_tokens_per_run,
        )
        text, status, error, usage = "", "failed", None, Usage()
        try:
            async with asyncio.timeout(spec.limits.timeout_s):
                async for event in self._loop.run(loop):
                    data = event.data
                    if event.type is RunEventType.MESSAGE and data.get("role") == "assistant":
                        content = data.get("content")
                        if isinstance(content, list):
                            text = text_of(content) or text
                    elif event.type is RunEventType.DONE:
                        status = str(data["status"])
                        err = data.get("error")
                        error = str(err) if err else None
                        loop_usage = data.get("usage")
                        usage = loop_usage if isinstance(loop_usage, Usage) else Usage()
        except TimeoutError:
            error = "Quá thời gian."
        except AppError as exc:
            error = exc.message
        return EphemeralResult(
            text=text, tools_used=list(tools.calls), status=status, error=error, usage=usage
        )
