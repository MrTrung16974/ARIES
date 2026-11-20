"""Use case: chat với một agent — chuẩn bị run, chạy vòng lặp, lưu lịch sử, ghi usage/audit."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from aiagent.modules.agents.application import AgentService
from aiagent.modules.agents.domain import Agent, AgentSpec, AgentVersion, render_system_prompt
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.conversations.application import ConversationService
from aiagent.modules.conversations.domain import Run, RunStatus, Thread
from aiagent.modules.identity.domain import Principal
from aiagent.modules.runtime.application.router import ModelRouter
from aiagent.modules.runtime.application.tool_gateway import BoundTools, ToolGateway
from aiagent.modules.runtime.domain import (
    AgentLoopPort,
    ChatMessage,
    ChatModelPort,
    LoopInput,
    ModelRequest,
    RunEvent,
    RunEventType,
    estimate_cost,
    history_for_model,
    pending_tool_uses,
    text_of,
)
from aiagent.plugin_sdk import ToolContext
from aiagent.shared_kernel.errors import AppError, ConflictError
from aiagent.shared_kernel.usage import Usage

logger = logging.getLogger(__name__)

MAX_USER_MESSAGE_CHARS = 20_000


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
    tools: BoundTools
    history: list[ChatMessage]
    user_message: str


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

        stored = await self._conv.repo.list_messages(principal.tenant_id, thread.id)
        history: list[ChatMessage] = [{"role": m.role, "content": m.content} for m in stored]

        run = await self._conv.repo.start_run(
            principal.tenant_id,
            thread.id,
            agent_id,
            version.version,
            principal.subject,
            version.spec.model.model,
        )
        # Run trước bị ngắt khi chờ tool → thêm tool_result lỗi (append-only) cho lịch sử hợp lệ.
        pending = pending_tool_uses(history)
        if pending:
            repair = [
                {
                    "type": "tool_result",
                    "tool_use_id": b["id"],
                    "is_error": True,
                    "content": "Lần chạy trước bị gián đoạn, tool chưa được thực thi.",
                }
                for b in pending
            ]
            await self._conv.repo.append_message(
                principal.tenant_id, thread.id, run.id, "user", repair
            )
            history.append({"role": "user", "content": repair})

        tools = self._gateway.bind(
            version.spec,
            ToolContext(
                tenant_id=principal.tenant_id,
                user_id=principal.subject,
                agent_id=str(agent_id),
                run_id=str(run.id),
            ),
        )
        return PreparedRun(principal, agent, version, thread, run, model, tools, history, message)

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
            },
        )
        try:
            user_content = [{"type": "text", "text": p.user_message}]
            await self._conv.repo.append_message(
                tenant, p.thread.id, p.run.id, "user", user_content
            )
            messages = history_for_model([*p.history, {"role": "user", "content": user_content}])
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
                async for event in self._loop.run(loop):
                    data = event.data
                    if event.type is RunEventType.MESSAGE:
                        content = data.get("content")
                        if isinstance(content, list) and content:
                            await self._conv.repo.append_message(
                                tenant, p.thread.id, p.run.id, str(data["role"]), content
                            )
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

        if error and status is not RunStatus.COMPLETED:
            yield RunEvent(RunEventType.ERROR, {"message": error, "status": status.value})
        yield RunEvent(
            RunEventType.USAGE, {**usage.as_dict(), "cost_usd": estimate_cost(model_name, usage)}
        )
        yield RunEvent(
            RunEventType.DONE,
            {"status": status.value, "run_id": str(p.run.id), "thread_id": str(p.thread.id)},
        )

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
            outcome=Outcome.SUCCESS if status is RunStatus.COMPLETED else Outcome.FAILURE,
            resource_type="agent",
            resource_id=str(p.agent.id),
            details={
                "run_id": str(p.run.id),
                "thread_id": str(p.thread.id),
                "version": p.version.version,
                "status": status.value,
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
        tools = self._gateway.bind(
            spec,
            ToolContext(
                tenant_id=principal.tenant_id,
                user_id=principal.subject,
                agent_id="draft",
                run_id=run_label,
            ),
            dry_run=True,
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
