"""ArchitectService — pipeline tạo/sửa agent bằng ngôn ngữ tự nhiên (docs/agent-architect.md).

create: understand → clarify? → plan → compose → validate (≤ 2 vòng tự sửa) → eval → present
edit:   edit (JSON Patch có scope) → validate → chạy lại eval_suite → diff
publish: luôn qua `confirm_publish` (người dùng bấm xác nhận); eval chưa đạt cần override.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from aiagent.modules.agents.application import AgentService
from aiagent.modules.agents.domain import AgentSpec, CreatedVia
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.builder.application.events import BuilderEvent
from aiagent.modules.builder.domain import (
    ArchitectBrainPort,
    BuilderSession,
    BuilderSessionRepositoryPort,
    CapabilityPlan,
    Catalog,
    ConnectionChoice,
    EvalCase,
    EvalCaseResult,
    EvalReport,
    Intent,
    PatchError,
    PromptDraft,
    RequirementBrief,
    SessionStatus,
    SkillChoice,
    UsageMeter,
    apply_patch,
    diff_specs,
)
from aiagent.modules.identity.domain import Principal, Role
from aiagent.modules.runtime.application import RunAgentService
from aiagent.modules.runtime.domain import estimate_cost
from aiagent.shared_kernel.errors import AppError, ForbiddenError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

EVAL_THRESHOLD = 0.8
MAX_COMPOSE_ATTEMPTS = 3
EVAL_CONCURRENCY = 3


class CatalogProviderPort(Protocol):
    async def catalog_for(self, principal: Principal) -> Catalog: ...


def provider_of(model: str, providers: Mapping[str, str] | None = None) -> str:
    if providers and model in providers:
        return providers[model]
    return "fake" if model.startswith("fake-") else "anthropic"


def permissions_of(spec: AgentSpec, catalog: Catalog) -> list[dict[str, Any]]:
    """Quyền agent sẽ có — hiển thị trước khi publish."""
    needs = {r.value for r in spec.approval.require_for}
    out: list[dict[str, Any]] = []
    by_id = {s.id: s for s in catalog.skills}
    for binding in spec.skills:
        skill = by_id.get(binding.skill_id)
        if skill is None:
            continue
        for tool in skill.tools:
            if binding.allowed_tools and tool.name not in binding.allowed_tools:
                continue
            status = "needs_approval" if tool.risk in needs else "active"
            out.append({"skill": skill.id, "tool": tool.name, "risk": tool.risk, "status": status})
    conns = {c.id: c for c in catalog.connections}
    for cb in spec.connections:
        conn = conns.get(str(cb.connection_id))
        if conn is None:
            continue
        for tool in conn.tools:
            if cb.allowed_tools and tool.name not in cb.allowed_tools:
                continue
            status = "needs_approval" if tool.risk in needs else "active"
            out.append(
                {
                    "skill": conn.connector_id,
                    "connection": conn.name,
                    "tool": tool.name,
                    "risk": tool.risk,
                    "status": status,
                }
            )
    kbs = {k.id: k for k in catalog.knowledge_bases}
    for kb_id in spec.knowledge_bases:
        kb = kbs.get(kb_id)
        if kb is not None:
            out.append(
                {
                    "skill": "knowledge",
                    "connection": kb.name,
                    "tool": "kb.search",
                    "risk": "read",
                    "status": "active",
                }
            )
    return out


def summarize(spec: AgentSpec) -> str:
    skills = ", ".join(s.skill_id for s in spec.skills) or "không có"
    conns = f" · {len(spec.connections)} kết nối" if spec.connections else ""
    if spec.knowledge_bases:
        conns += f" · {len(spec.knowledge_bases)} kho tri thức"
    return (
        f"{spec.name} — {spec.description or 'chưa có mô tả'}\n"
        f"Model: {spec.model.model} (effort {spec.model.effort.value}) · Skill: {skills}{conns} · "
        f"{len(spec.rules)} quy tắc · dữ liệu {spec.data_classification.value}"
    )


class ArchitectService:
    def __init__(
        self,
        sessions: BuilderSessionRepositoryPort,
        brain: ArchitectBrainPort,
        agents: AgentService,
        runs: RunAgentService,
        catalog: CatalogProviderPort,
        audit: AuditService,
        *,
        cost_model: str = "claude-opus-5-5",
    ) -> None:
        self._sessions = sessions
        self._brain = brain
        self._agents = agents
        self._runs = runs
        self._catalog = catalog
        self._audit = audit
        self._cost_model = cost_model

    @property
    def brain_name(self) -> str:
        return self._brain.name

    # ================================================================== sessions
    @staticmethod
    def _require_builder(principal: Principal) -> None:
        if not principal.has_any_role(Role.BUILDER, Role.ADMIN):
            raise ForbiddenError("Chỉ Builder trở lên được dùng Agent Architect.")

    async def create_session(
        self, principal: Principal, agent_id: UUID | None = None
    ) -> BuilderSession:
        self._require_builder(principal)
        base_version, draft = None, None
        if agent_id is not None:
            agent = await self._agents.get(principal, agent_id)
            if not self._agents.can_edit(principal, agent):
                raise ForbiddenError()
            version = await self._agents.version(principal, agent_id, agent.latest_version)
            base_version, draft = version.version, version.spec.model_dump(mode="json")
        return await self._sessions.create(
            principal.tenant_id, principal.subject, agent_id, base_version, draft
        )

    async def owned(self, principal: Principal, session_id: UUID) -> BuilderSession:
        self._require_builder(principal)
        session = await self._sessions.get(principal.tenant_id, session_id)
        if session is None or session.user_id != principal.subject:
            raise NotFoundError("Không tìm thấy phiên Architect.")
        return session

    async def list_sessions(self, principal: Principal, limit: int = 30) -> list[BuilderSession]:
        self._require_builder(principal)
        return await self._sessions.list_for_user(principal.tenant_id, principal.subject, limit)

    async def history(self, principal: Principal, session_id: UUID) -> list[dict[str, Any]]:
        await self.owned(principal, session_id)
        return await self._sessions.messages(principal.tenant_id, session_id)

    async def view(self, principal: Principal, session: BuilderSession) -> dict[str, Any]:
        """Trạng thái hiển thị cho UI (preview bên phải)."""
        data: dict[str, Any] = {
            "id": str(session.id),
            "status": session.status.value,
            "agent_id": str(session.agent_id) if session.agent_id else None,
            "base_version": session.base_version,
            "brief": session.brief,
            "questions": session.questions,
            "eval": session.eval_report,
            "spec": session.draft_spec,
            "summary": None,
            "permissions": [],
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "brain": self._brain.name,
        }
        if session.draft_spec:
            spec = AgentSpec.model_validate(session.draft_spec)
            data["summary"] = summarize(spec)
            data["permissions"] = permissions_of(spec, (await self._catalog.catalog_for(principal)))
        return data

    # ================================================================== main loop
    async def handle(
        self,
        principal: Principal,
        session_id: UUID,
        *,
        message: str | None = None,
        answers: dict[str, str] | None = None,
    ) -> AsyncIterator[BuilderEvent]:
        session = await self.owned(principal, session_id)
        text = self._user_text(session, message, answers)
        await self._sessions.add_message(principal.tenant_id, session.id, "user", text, [])
        meter = UsageMeter()
        emitted: list[dict[str, Any]] = []
        reply_parts: list[str] = []

        async def run() -> AsyncIterator[BuilderEvent]:
            if answers or session.draft_spec is None:
                intent, test_message = Intent.CREATE, None
            else:
                yield BuilderEvent("status", {"text": "Đang hiểu yêu cầu…"})
                result = await self._brain.classify(text, True, meter)
                intent, test_message = result.intent, result.test_message

            if intent is Intent.CREATE and session.draft_spec is not None and not answers:
                intent = Intent.EDIT  # đã có bản nháp: mô tả thêm được hiểu là sửa
            if intent is Intent.CREATE:
                async for ev in self._create(principal, session, meter):
                    yield ev
            elif intent is Intent.EDIT:
                async for ev in self._edit(principal, session, text, meter):
                    yield ev
            elif intent is Intent.EXPLAIN:
                yield await self._explain(principal, session)
            elif intent is Intent.TEST:
                async for ev in self._test(principal, session, test_message or text):
                    yield ev
            elif intent is Intent.PUBLISH:
                yield self._publish_prompt(session)
            else:
                yield BuilderEvent(
                    "message",
                    {
                        "text": (
                            "Mình là Agent Architect: mô tả agent bạn muốn tạo, hoặc yêu cầu sửa agent "
                            "đang dựng (ví dụ: “thêm quy tắc không hứa hoàn tiền”)."
                        )
                    },
                )

        try:
            async for event in run():
                if event.type == "message":
                    reply_parts.append(str(event.data.get("text", "")))
                if event.type not in ("status", "eval_progress"):
                    emitted.append({"type": event.type})
                yield event
        except AppError as exc:
            reply_parts.append(exc.message)
            yield BuilderEvent("error", {"message": exc.message, "code": exc.code})
        except Exception:
            logger.exception("Architect lỗi ở phiên %s", session.id)
            yield BuilderEvent("error", {"message": "Architect gặp lỗi nội bộ."})
        finally:
            await asyncio.shield(self._finish(principal, session, reply_parts, emitted))

        cost = (
            estimate_cost(self._cost_model, meter.usage) if self._brain.name != "heuristic" else 0
        )
        yield BuilderEvent(
            "usage", {**meter.usage.as_dict(), "calls": meter.calls, "cost_usd": cost}
        )
        yield BuilderEvent("done", {"status": session.status.value, "session_id": str(session.id)})

    async def _finish(
        self,
        principal: Principal,
        session: BuilderSession,
        reply: list[str],
        emitted: list[dict[str, Any]],
    ) -> None:
        try:
            await self._sessions.save(session)
            await self._sessions.add_message(
                principal.tenant_id,
                session.id,
                "architect",
                "\n".join(p for p in reply if p),
                emitted,
            )
        except Exception:
            logger.exception("Không lưu được phiên Architect %s", session.id)

    @staticmethod
    def _user_text(
        session: BuilderSession, message: str | None, answers: dict[str, str] | None
    ) -> str:
        if answers:
            by_id = {q.get("id"): q.get("question") for q in session.questions}
            return "Trả lời: " + "; ".join(
                f"{by_id.get(k, k)} → {v}" for k, v in answers.items() if str(v).strip()
            )
        text = (message or "").strip()
        if not text or len(text) > 8000:
            raise ValidationError("Tin nhắn trống hoặc quá dài.")
        return text

    # ================================================================== create
    async def _create(
        self, principal: Principal, session: BuilderSession, meter: UsageMeter
    ) -> AsyncIterator[BuilderEvent]:
        history = await self._sessions.messages(principal.tenant_id, session.id)
        user_messages = [m["content"] for m in history if m["role"] == "user"]
        yield BuilderEvent("status", {"text": "Đang phân tích yêu cầu…"})
        brief = await self._brain.understand(user_messages, session.clarify_rounds == 0, meter)
        session.brief = brief.model_dump(mode="json")
        yield BuilderEvent("brief", session.brief)

        if brief.questions:
            session.questions = [q.model_dump(mode="json") for q in brief.questions]
            session.clarify_rounds += 1
            session.status = SessionStatus.AWAITING_USER
            yield BuilderEvent("clarify", {"questions": session.questions})
            yield BuilderEvent(
                "message", {"text": "Mình cần thêm một chút thông tin trước khi dựng."}
            )
            return
        session.questions = []

        catalog = await self._catalog.catalog_for(principal)
        if not catalog.models:
            raise ValidationError(
                "Chưa có model nào khả dụng (cấu hình API key Claude/Gemini/Groq/OpenRouter "
                "hoặc Ollama)."
            )
        yield BuilderEvent("status", {"text": "Đang chọn năng lực cho agent…"})
        plan = self._sanitize_plan(await self._brain.plan(brief, catalog, meter), catalog)

        yield BuilderEvent("status", {"text": "Đang viết cấu hình agent…"})
        spec = await self._compose_valid(brief, plan, meter, catalog.model_providers)
        before = session.draft_spec
        session.draft_spec = spec.model_dump(mode="json")
        session.status = SessionStatus.DRAFTING
        yield BuilderEvent(
            "spec_draft",
            {
                "summary": summarize(spec),
                "spec": session.draft_spec,
                "permissions": permissions_of(spec, catalog),
                "assumptions": brief.assumptions,
                "missing": [m.model_dump(mode="json") for m in plan.missing],
                "changes": [c.human() for c in diff_specs(before, session.draft_spec)]
                if before
                else [],
            },
        )

        async for ev in self._evaluate(principal, session, spec, brief, None, meter):
            yield ev
        session.status = SessionStatus.READY
        report = EvalReport.model_validate(session.eval_report)
        yield BuilderEvent(
            "message",
            {
                "text": (
                    f"Đã dựng bản nháp “{spec.name}”. Eval {report.summary}"
                    f"{'' if report.passed else ' — chưa đạt ngưỡng, nên sửa thêm trước khi publish'}. "
                    "Bạn có thể yêu cầu sửa bằng lời, gõ “thử: <câu hỏi>” hoặc bấm Publish."
                )
            },
        )

    @staticmethod
    def _sanitize_plan(plan: CapabilityPlan, catalog: Catalog) -> CapabilityPlan:
        """Lớp chặn cuối: bỏ mọi thứ ngoài catalog dù model có trả về."""
        tools_by_skill = {s.id: {t.name for t in s.tools} for s in catalog.skills}
        kept: list[SkillChoice] = []
        for choice in plan.skills:
            if choice.skill_id not in tools_by_skill:
                continue
            choice.allowed_tools = [
                t for t in choice.allowed_tools if t in tools_by_skill[choice.skill_id]
            ]
            if choice.skill_id not in {k.skill_id for k in kept}:
                kept.append(choice)
        plan.skills = kept
        tools_by_conn = {c.id: {t.name for t in c.tools} for c in catalog.connections}
        kept_conns: list[ConnectionChoice] = []
        for cc in plan.connections:
            if cc.connection_id not in tools_by_conn:
                continue
            cc.allowed_tools = [t for t in cc.allowed_tools if t in tools_by_conn[cc.connection_id]]
            if cc.connection_id not in {k.connection_id for k in kept_conns}:
                kept_conns.append(cc)
        plan.connections = kept_conns
        known_kbs = set(catalog.knowledge_base_ids())
        plan.knowledge_bases = list(
            dict.fromkeys(k for k in plan.knowledge_bases if k in known_kbs)
        )
        if plan.model not in catalog.models:
            plan.model = catalog.models[0]
        return plan

    async def _compose_valid(
        self,
        brief: RequirementBrief,
        plan: CapabilityPlan,
        meter: UsageMeter,
        providers: Mapping[str, str] | None = None,
    ) -> AgentSpec:
        errors: list[str] = []
        for _ in range(MAX_COMPOSE_ATTEMPTS):
            draft = await self._brain.compose(brief, plan, errors, meter)
            spec, errors = self._assemble(brief, plan, draft, providers)
            if spec is not None:
                return spec
        raise ValidationError("Không dựng được cấu hình hợp lệ: " + "; ".join(errors))

    def _assemble(
        self,
        brief: RequirementBrief,
        plan: CapabilityPlan,
        draft: PromptDraft,
        providers: Mapping[str, str] | None = None,
    ) -> tuple[AgentSpec | None, list[str]]:
        raw = {
            "name": draft.name.strip()[:80] or brief.suggested_name[:80] or "Agent mới",
            "description": draft.description.strip()[:1000],
            "model": {
                "provider": provider_of(plan.model, providers),
                "model": plan.model,
                "effort": plan.effort,
            },
            "persona": draft.persona[:2000],
            "instructions": draft.instructions[:20000],
            "rules": [r[:500] for r in draft.rules][:50],
            "output_format": {"language": brief.language[:16] or "vi", "style": draft.style[:500]},
            "opening_statement": draft.opening_statement[:1000],
            "starter_prompts": [s[:500] for s in draft.starter_prompts][:6],
            "when_to_use": draft.when_to_use.strip()[:300],
            "skills": [
                {"skill_id": s.skill_id, "allowed_tools": s.allowed_tools, "config": {}}
                for s in plan.skills
            ],
            "connections": [
                {"connection_id": c.connection_id, "allowed_tools": c.allowed_tools}
                for c in plan.connections
            ],
            "knowledge_bases": list(plan.knowledge_bases),
        }
        try:
            spec = AgentSpec.model_validate(raw)
            self._agents.validate(spec)
        except PydanticValidationError as exc:
            return None, [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:10]
        except ValidationError as exc:
            return None, [exc.message]
        return spec, []

    # ================================================================== edit
    async def _edit(
        self, principal: Principal, session: BuilderSession, instruction: str, meter: UsageMeter
    ) -> AsyncIterator[BuilderEvent]:
        if session.draft_spec is None:
            raise ValidationError("Chưa có bản nháp để sửa.")
        catalog = await self._catalog.catalog_for(principal)
        yield BuilderEvent("status", {"text": "Đang tính thay đổi…"})
        proposal = await self._brain.edit(session.draft_spec, instruction, catalog, meter)
        try:
            patched = apply_patch(session.draft_spec, proposal.ops, proposal.scope)
            spec = AgentSpec.model_validate(patched)
            self._agents.validate(spec)
        except (PatchError, PydanticValidationError, ValidationError) as exc:
            detail = exc.message if isinstance(exc, ValidationError) else str(exc)
            yield BuilderEvent("validation", {"ok": False, "errors": [detail[:500]]})
            yield BuilderEvent("message", {"text": f"Không áp dụng được thay đổi: {detail[:300]}"})
            return

        new_dump = spec.model_dump(mode="json")
        changes = diff_specs(session.draft_spec, new_dump)
        if not changes:
            yield BuilderEvent("message", {"text": "Yêu cầu không làm thay đổi cấu hình."})
            return
        old_report = EvalReport.model_validate(session.eval_report) if session.eval_report else None
        session.draft_spec = new_dump
        yield BuilderEvent(
            "spec_diff",
            {
                "summary": proposal.summary,
                "changes": [c.human() for c in changes],
                "spec": new_dump,
                "permissions": permissions_of(spec, catalog),
            },
        )
        cases = [r.case for r in old_report.results] if old_report else None
        brief = RequirementBrief.model_validate(session.brief) if session.brief else None
        async for ev in self._evaluate(principal, session, spec, brief, cases, meter):
            yield ev
        session.status = SessionStatus.READY
        report = EvalReport.model_validate(session.eval_report)
        note = ""
        if old_report and report.score < old_report.score:
            note = (
                f" ⚠ Điểm eval giảm từ {old_report.score:.0%} xuống {report.score:.0%} — "
                "cân nhắc hoàn tác hoặc sửa tiếp."
            )
        yield BuilderEvent(
            "message", {"text": f"Đã cập nhật: {proposal.summary}. Eval {report.summary}.{note}"}
        )

    # ================================================================== eval / test
    async def _evaluate(
        self,
        principal: Principal,
        session: BuilderSession,
        spec: AgentSpec,
        brief: RequirementBrief | None,
        cases: list[EvalCase] | None,
        meter: UsageMeter,
    ) -> AsyncIterator[BuilderEvent]:
        spec_dump = spec.model_dump(mode="json")
        if cases is None:
            yield BuilderEvent("status", {"text": "Đang sinh tình huống kiểm thử…"})
            cases = await self._brain.generate_cases(brief, spec_dump, meter)
        total = len(cases)
        yield BuilderEvent("eval_progress", {"done": 0, "total": total})
        semaphore = asyncio.Semaphore(EVAL_CONCURRENCY)

        async def one(case: EvalCase) -> EvalCaseResult:
            async with semaphore:
                run = await self._runs.run_ephemeral(
                    principal, spec, case.input, run_label=f"eval:{session.id}:{case.id}"
                )
                if run.error and not run.text:
                    return EvalCaseResult(
                        case=case,
                        answer="",
                        tools_used=run.tools_used,
                        passed=False,
                        score=0.0,
                        reason=run.error,
                        error=run.error,
                    )
                verdict = await self._brain.judge(case, run.text, run.tools_used, spec_dump, meter)
                return EvalCaseResult(
                    case=case,
                    answer=run.text[:2000],
                    tools_used=run.tools_used,
                    passed=verdict.passed,
                    score=verdict.score,
                    reason=verdict.reason,
                    error=run.error,
                )

        results: list[EvalCaseResult] = []
        for done, task in enumerate(asyncio.as_completed([one(c) for c in cases]), start=1):
            results.append(await task)
            yield BuilderEvent("eval_progress", {"done": done, "total": total})
        order = {c.id: i for i, c in enumerate(cases)}
        results.sort(key=lambda r: order.get(r.case.id, 0))
        score = sum(r.score for r in results) / total if total else 0.0
        rules_ok = all(r.passed for r in results if r.case.kind == "rule")
        report = EvalReport(
            score=round(score, 3),
            passed=score >= EVAL_THRESHOLD and rules_ok,
            results=results,
            threshold=EVAL_THRESHOLD,
        )
        session.eval_report = report.model_dump(mode="json")
        yield BuilderEvent("eval_result", {**session.eval_report, "summary": report.summary})

    async def _test(
        self, principal: Principal, session: BuilderSession, message: str
    ) -> AsyncIterator[BuilderEvent]:
        if session.draft_spec is None:
            yield BuilderEvent("message", {"text": "Chưa có bản nháp để thử."})
            return
        spec = AgentSpec.model_validate(session.draft_spec)
        yield BuilderEvent("status", {"text": "Đang chạy thử…"})
        run = await self._runs.run_ephemeral(
            principal, spec, message, run_label=f"test:{session.id}"
        )
        yield BuilderEvent(
            "test_result",
            {
                "input": message,
                "answer": run.text,
                "tools_used": run.tools_used,
                "error": run.error,
            },
        )

    async def _explain(self, principal: Principal, session: BuilderSession) -> BuilderEvent:
        if session.draft_spec is None:
            return BuilderEvent("message", {"text": "Chưa có bản nháp."})
        spec = AgentSpec.model_validate(session.draft_spec)
        perms = permissions_of(spec, (await self._catalog.catalog_for(principal)))
        lines = (
            [summarize(spec), "Quyền:"]
            + [
                f"- {p['tool']} ({p['risk']}){' — cần duyệt, đang ẩn' if p['status'] != 'active' else ''}"
                for p in perms
            ]
            + (["Quy tắc:"] + [f"- {r}" for r in spec.rules] if spec.rules else [])
        )
        return BuilderEvent(
            "message", {"text": "\n".join(lines if perms else lines[:1] + lines[2:])}
        )

    def _publish_prompt(self, session: BuilderSession) -> BuilderEvent:
        report = EvalReport.model_validate(session.eval_report) if session.eval_report else None
        return BuilderEvent(
            "confirm_required",
            {
                "action": "publish",
                "eval_passed": bool(report and report.passed),
                "eval_summary": report.summary if report else "chưa chạy eval",
            },
        )

    # ================================================================== publish
    async def confirm_publish(
        self, principal: Principal, session_id: UUID, *, override_eval: bool, note: str = ""
    ) -> dict[str, Any]:
        session = await self.owned(principal, session_id)
        if session.draft_spec is None:
            raise ValidationError("Chưa có bản nháp để publish.")
        report = EvalReport.model_validate(session.eval_report) if session.eval_report else None
        passed = bool(report and report.passed)
        if not passed and not override_eval:
            raise ValidationError(
                f"Eval chưa đạt ({report.summary if report else 'chưa chạy'}). "
                "Xác nhận publish dù chưa đạt để tiếp tục."
            )
        spec = AgentSpec.model_validate(session.draft_spec)
        change_note = note or f"Architect · eval {report.summary if report else 'n/a'}"
        if session.agent_id is None:
            agent, version = await self._agents.create(
                principal, spec, via=CreatedVia.ARCHITECT, note=change_note
            )
            session.agent_id = agent.id
        else:
            version = await self._agents.update(
                principal,
                session.agent_id,
                spec,
                expected_version=session.base_version,
                via=CreatedVia.ARCHITECT,
                note=change_note,
            )
        await self._agents.publish(principal, session.agent_id, version.version)
        session.base_version = version.version
        session.status = SessionStatus.PUBLISHED
        await self._sessions.save(session)
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action="architect.published",
            outcome=Outcome.SUCCESS,
            resource_type="agent",
            resource_id=str(session.agent_id),
            details={
                "session_id": str(session.id),
                "version": version.version,
                "eval_score": report.score if report else None,
                "eval_passed": passed,
                "override_eval": override_eval and not passed,
            },
        )
        return {"agent_id": str(session.agent_id), "version": version.version, "published": True}
