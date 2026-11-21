"""Use case tự động hóa (ADR-0021): quản lý schedule, nhận lịch đến hạn, chạy agent."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from aiagent.modules.agents.application import AgentService
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.automation.domain import (
    DEFAULT_TZ,
    GMAIL_INTERVALS,
    KINDS,
    MAX_FAILURES,
    MAX_SCHEDULES_PER_USER,
    AgentRunPort,
    CronError,
    CronExpr,
    Due,
    JobQueuePort,
    Schedule,
    ScheduleRepositoryPort,
    ScheduleRun,
    TenantListPort,
    TriggerSourcePort,
    validate,
)
from aiagent.modules.connections.application import ConnectionService
from aiagent.modules.identity.domain import Principal, Role
from aiagent.modules.runtime.application.tool_gateway import wrap_untrusted
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import AppError, ForbiddenError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

MAX_PROMPT = 4000
MAX_FILTER = 100
FAILED = ("failed",)


def run_principal(tenant_id: str, owner_id: str) -> Principal:
    """Danh tính khi chạy vắng mặt: chính chủ schedule nhưng KHÔNG mang vai trò (ADR-0021)."""
    return Principal(
        subject=owner_id,
        tenant_id=tenant_id,
        username=None,
        email=None,
        roles=frozenset(),
    )


def gmail_cron(minutes: int) -> str:
    return "0 * * * *" if minutes == 60 else f"*/{minutes} * * * *"


class AutomationService:
    def __init__(
        self,
        repo: ScheduleRepositoryPort,
        agents: AgentService,
        connections: ConnectionService,
        runner: AgentRunPort,
        triggers: TriggerSourcePort,
        queue: JobQueuePort,
        tenants: TenantListPort,
        audit: AuditService,
    ) -> None:
        self._repo = repo
        self._agents = agents
        self._connections = connections
        self._runner = runner
        self._triggers = triggers
        self._queue = queue
        self._tenants = tenants
        self._audit = audit

    # ------------------------------------------------------------------ kiểm tra
    @staticmethod
    def _next(s: Schedule, after: datetime) -> datetime | None:
        try:
            return CronExpr.parse(s.cron).next_after(after, s.timezone)
        except CronError:
            return None

    async def _check_agent(self, tenant_id: str, owner_id: str, agent_id: UUID) -> None:
        # Chạy thử quyền đúng như lúc vắng mặt: agent phải publish và nhìn thấy được khi không có vai trò.
        await self._agents.resolve_for_run(
            run_principal(tenant_id, owner_id), agent_id, draft=False
        )

    async def _check_connection(self, tenant_id: str, owner_id: str, cid: UUID | None) -> None:
        if cid is None:
            raise ValidationError("Trigger Gmail cần chọn một kết nối Gmail.")
        usable = await self._connections.usable(run_principal(tenant_id, owner_id), [str(cid)])
        if not usable or usable[0].connector_id != "gmail":
            raise ValidationError("Kết nối Gmail không tồn tại hoặc bạn không được dùng.")

    @staticmethod
    def _clean_filters(config: dict[str, Any] | None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in ("from_contains", "subject_contains"):
            value = str((config or {}).get(key) or "").strip()
            if len(value) > MAX_FILTER:
                raise ValidationError(f"Bộ lọc {key} tối đa {MAX_FILTER} ký tự.")
            if value:
                out[key] = value
        return out

    def _owned(self, principal: Principal, s: Schedule | None, *, manage: bool = False) -> Schedule:
        if s is None:
            raise NotFoundError("Không tìm thấy lịch chạy.")
        if s.owner_id == principal.subject:
            return s
        if manage and principal.has_any_role(Role.ADMIN):
            return s  # admin chỉ được xem/tắt/xóa, không sửa nội dung lịch của người khác
        raise NotFoundError("Không tìm thấy lịch chạy.")

    # ------------------------------------------------------------------ quản lý
    async def list_schedules(
        self, principal: Principal, *, everyone: bool = False
    ) -> list[Schedule]:
        if everyone and not principal.has_any_role(Role.ADMIN, Role.AUDITOR):
            raise ForbiddenError()
        return await self._repo.list_for(
            principal.tenant_id, None if everyone else principal.subject
        )

    async def get(self, principal: Principal, schedule_id: UUID) -> Schedule:
        s = await self._repo.get(principal.tenant_id, schedule_id)
        if s is not None and principal.has_any_role(Role.ADMIN, Role.AUDITOR):
            return s
        return self._owned(principal, s)

    async def create(
        self,
        principal: Principal,
        *,
        name: str,
        agent_id: UUID,
        kind: str,
        prompt: str,
        cron: str = "",
        timezone: str = DEFAULT_TZ,
        interval_minutes: int = 5,
        connection_id: UUID | None = None,
        trigger_config: dict[str, Any] | None = None,
    ) -> Schedule:
        tenant, owner = principal.tenant_id, principal.subject
        name, prompt = name.strip()[:100], prompt.strip()
        if not name:
            raise ValidationError("Hãy đặt tên cho lịch chạy.")
        if not prompt or len(prompt) > MAX_PROMPT:
            raise ValidationError(f"Lời nhắn cho agent phải có 1–{MAX_PROMPT} ký tự.")
        if kind not in KINDS:
            raise ValidationError("Kiểu kích hoạt phải là cron hoặc gmail.")
        if await self._repo.count_for_owner(tenant, owner) >= MAX_SCHEDULES_PER_USER:
            raise ValidationError(f"Mỗi người tối đa {MAX_SCHEDULES_PER_USER} lịch chạy.")
        await self._check_agent(tenant, owner, agent_id)
        config: dict[str, Any] = {}
        if kind == "gmail":
            if interval_minutes not in GMAIL_INTERVALS:
                raise ValidationError("Chu kỳ kiểm tra mail không hợp lệ.")
            cron = gmail_cron(interval_minutes)
            await self._check_connection(tenant, owner, connection_id)
            config = self._clean_filters(trigger_config)
        else:
            connection_id = None
        try:
            expr = validate(cron, timezone, max_per_hour=60 if kind == "gmail" else 12)
        except CronError as exc:
            raise ValidationError(str(exc)) from None
        now = utcnow()
        s = Schedule(
            id=uuid4(),
            tenant_id=tenant,
            owner_id=owner,
            agent_id=agent_id,
            name=name,
            kind=kind,
            cron=cron.strip(),
            timezone=timezone,
            prompt=prompt,
            connection_id=connection_id,
            trigger_config=config,
            state={},
            enabled=True,
            next_run_at=expr.next_after(now, timezone),
            last_run_at=None,
            last_status=None,
            failure_count=0,
            created_at=now,
            updated_at=now,
        )
        created = await self._repo.create(s)
        await self._record(principal, "schedule.created", created)
        return created

    async def update(
        self,
        principal: Principal,
        schedule_id: UUID,
        *,
        enabled: bool | None = None,
        name: str | None = None,
        prompt: str | None = None,
        cron: str | None = None,
        timezone: str | None = None,
        trigger_config: dict[str, Any] | None = None,
    ) -> Schedule:
        s = self._owned(
            principal,
            await self._repo.get(principal.tenant_id, schedule_id),
            manage=enabled is False
            and all(v is None for v in (name, prompt, cron, timezone, trigger_config)),
        )
        changes: dict[str, Any] = {}
        if name is not None:
            if not name.strip():
                raise ValidationError("Tên không được trống.")
            changes["name"] = name.strip()[:100]
        if prompt is not None:
            if not prompt.strip() or len(prompt) > MAX_PROMPT:
                raise ValidationError(f"Lời nhắn cho agent phải có 1–{MAX_PROMPT} ký tự.")
            changes["prompt"] = prompt.strip()
        if s.kind == "cron" and (cron is not None or timezone is not None):
            new_cron, new_tz = cron or s.cron, timezone or s.timezone
            try:
                validate(new_cron, new_tz)
            except CronError as exc:
                raise ValidationError(str(exc)) from None
            changes.update(cron=new_cron.strip(), timezone=new_tz)
        if trigger_config is not None and s.kind == "gmail":
            changes["trigger_config"] = self._clean_filters(trigger_config)
        if enabled is not None:
            changes["enabled"] = enabled
            if enabled:
                await self._check_agent(s.tenant_id, s.owner_id, s.agent_id)
                if s.kind == "gmail":
                    await self._check_connection(s.tenant_id, s.owner_id, s.connection_id)
                changes["failure_count"] = 0
        updated = replace(s, **changes)
        updated = replace(
            updated,
            next_run_at=self._next(updated, utcnow()) if updated.enabled else None,
            updated_at=utcnow(),
        )
        saved = await self._repo.save(updated)
        await self._record(principal, "schedule.updated", saved)
        return saved

    async def delete(self, principal: Principal, schedule_id: UUID) -> None:
        s = self._owned(
            principal, await self._repo.get(principal.tenant_id, schedule_id), manage=True
        )
        await self._repo.delete(s.tenant_id, s.id)
        await self._record(principal, "schedule.deleted", s)

    async def runs(
        self, principal: Principal, schedule_id: UUID, limit: int = 50
    ) -> list[ScheduleRun]:
        s = await self.get(principal, schedule_id)
        return await self._repo.list_runs(s.tenant_id, s.id, limit)

    async def run_now(self, principal: Principal, schedule_id: UUID) -> None:
        s = self._owned(principal, await self._repo.get(principal.tenant_id, schedule_id))
        await self._queue.enqueue_schedule(s.tenant_id, s.id, utcnow())

    async def _record(self, principal: Principal, action: str, s: Schedule) -> None:
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action=action,
            outcome=Outcome.SUCCESS,
            resource_type="schedule",
            resource_id=str(s.id),
            details={"agent_id": str(s.agent_id), "kind": s.kind, "enabled": s.enabled},
        )

    # ------------------------------------------------------------------ worker
    async def tick(self, now: datetime | None = None) -> list[Due]:
        """Cron mỗi phút: nhận lịch đến hạn ở mọi tenant và đẩy job chạy."""
        now = now or utcnow()
        claimed: list[Due] = []
        for tenant in await self._tenants.tenant_ids():
            due = await self._repo.claim_due(tenant, now, lambda s: self._next(s, now))
            for d in due:
                await self._queue.enqueue_schedule(d.tenant_id, d.schedule_id, d.fired_at)
            claimed += due
        return claimed

    async def resume_waiting(self) -> int:
        """Cron mỗi phút: lần chạy vắng mặt đã được duyệt xong → chạy tiếp, không cần ai mở chat.

        Trả số lần chạy được tiếp tục.
        """
        resumed = 0
        for tenant in await self._tenants.tenant_ids():
            for w in await self._repo.waiting_runs(tenant):
                principal = run_principal(tenant, w.owner_id)
                try:
                    out = await self._runner.resume_once(principal, w.thread_id)
                except AppError as exc:  # agent bị xóa / có version mới / mất quyền…
                    await self._repo.finish_run(
                        tenant, w.run_id, status="failed", thread_id=w.thread_id, error=exc.message
                    )
                    continue
                except Exception:
                    logger.warning("Chạy tiếp sau duyệt lỗi", extra={"run_id": str(w.run_id)})
                    continue
                if out.status == "awaiting_approval":
                    continue  # chủ lịch chưa quyết định hết
                status = "completed" if out.status == "stale" else out.status
                await self._repo.finish_run(
                    tenant, w.run_id, status=status, thread_id=w.thread_id, error=None
                )
                if out.status != "stale":
                    resumed += 1
        return resumed

    async def _agent_gone(self, s: Schedule) -> bool:
        """Agent bị xóa / thôi publish → tắt lịch ngay (không đợi lỗi 5 lần)."""
        try:
            await self._check_agent(s.tenant_id, s.owner_id, s.agent_id)
        except (NotFoundError, ForbiddenError, ValidationError) as exc:
            await self._repo.record_result(
                s.tenant_id, s.id, status="agent_unavailable", failed=True, state=None, disable=True
            )
            await self._audit.record_safely(
                tenant_id=s.tenant_id,
                actor_id=None,
                action="schedule.auto_disabled",
                outcome=Outcome.FAILURE,
                resource_type="schedule",
                resource_id=str(s.id),
                details={"reason": "agent_unavailable", "detail": exc.message},
            )
            return True
        return False

    async def execute(self, tenant_id: str, schedule_id: UUID, fired_at: datetime) -> int:
        """Job `run_schedule`: chạy agent cho một lần kích hoạt. Trả số lần chạy agent."""
        s = await self._repo.get(tenant_id, schedule_id)
        if s is None or not s.enabled:
            return 0
        if await self._agent_gone(s):
            return 0
        principal = run_principal(s.tenant_id, s.owner_id)
        try:
            if s.kind == "gmail":
                return await self._execute_gmail(s, principal)
            ref = fired_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")
            ok = await self._run(s, principal, ref, s.name, s.prompt)
            await self._finish(s, ok, state=None)
            return 1 if ok is not None else 0
        except Exception as exc:  # lỗi hạ tầng/nguồn ngoài: ghi nhận, không làm chết worker
            logger.warning("Lịch chạy lỗi", extra={"schedule_id": str(s.id), "error": repr(exc)})
            await self._finish(s, "failed", state=None)
            return 0

    async def _execute_gmail(self, s: Schedule, principal: Principal) -> int:
        if s.connection_id is None:
            raise AppError("Thiếu kết nối Gmail.")
        items, state = await self._triggers.poll(
            principal, s.connection_id, dict(s.state), dict(s.trigger_config)
        )
        status: str | None = "idle"
        count = 0
        for item in items:
            message = f"{s.prompt}\n\n{wrap_untrusted('Gmail', item.content)}"
            result = await self._run(s, principal, item.ref, item.title, message)
            if result is not None:
                count += 1
                status = result if status in ("idle", "completed") else status
        await self._finish(s, status, state=state)
        return count

    async def _run(
        self, s: Schedule, principal: Principal, ref: str, title: str, message: str
    ) -> str | None:
        """Một lần chạy agent; None nếu trigger_ref đã chạy rồi."""
        run = await self._repo.start_run(s.tenant_id, s.id, ref, title[:200])
        if run is None:
            return None
        try:
            done = await self._runner.run_once(principal, s.agent_id, message, f"⏰ {s.name}")
        except AppError as exc:
            await self._repo.finish_run(
                s.tenant_id, run.id, status="failed", thread_id=None, error=exc.message
            )
            return "failed"
        except Exception:
            await self._repo.finish_run(
                s.tenant_id, run.id, status="failed", thread_id=None, error="Lỗi hệ thống."
            )
            raise
        await self._repo.finish_run(
            s.tenant_id, run.id, status=done.status, thread_id=done.thread_id, error=None
        )
        return done.status

    async def _finish(
        self, s: Schedule, status: str | None, *, state: dict[str, Any] | None
    ) -> None:
        if status is None:
            return
        failed = status in FAILED
        disable = failed and s.failure_count + 1 >= MAX_FAILURES
        await self._repo.record_result(
            s.tenant_id, s.id, status=status, failed=failed, state=state, disable=disable
        )
        if disable:
            await self._audit.record_safely(
                tenant_id=s.tenant_id,
                actor_id=None,
                action="schedule.auto_disabled",
                outcome=Outcome.FAILURE,
                resource_type="schedule",
                resource_id=str(s.id),
                details={"failures": s.failure_count + 1},
            )
