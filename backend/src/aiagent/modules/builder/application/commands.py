"""Thực thi slash command — xác định, không gọi LLM. Cùng quyền/validate/audit như API."""

from __future__ import annotations

from typing import Any

from aiagent.modules.agents.application import AgentService
from aiagent.modules.agents.domain import Agent
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.builder.application.architect import ArchitectService
from aiagent.modules.builder.domain import HELP_TEXT, CommandKind, parse_command
from aiagent.modules.identity.domain import Principal
from aiagent.shared_kernel.errors import AppError, NotFoundError, ValidationError


class CommandService:
    def __init__(
        self, architect: ArchitectService, agents: AgentService, audit: AuditService
    ) -> None:
        self._architect = architect
        self._agents = agents
        self._audit = audit

    async def _find(self, principal: Principal, name: str) -> Agent:
        agents = await self._agents.list_agents(principal)
        wanted = name.strip().casefold()
        exact = [a for a in agents if a.name.casefold() == wanted]
        matches = exact or [a for a in agents if wanted in a.name.casefold()]
        if not matches:
            raise NotFoundError(f"Không tìm thấy agent “{name}”.")
        if len(matches) > 1:
            names = ", ".join(f"“{a.name}”" for a in matches[:5])
            raise ValidationError(f"Có nhiều agent khớp “{name}”: {names}. Hãy ghi đúng tên.")
        return matches[0]

    async def execute(self, principal: Principal, raw: str) -> dict[str, Any]:
        command = parse_command(raw)
        try:
            result = await self._execute(principal, command)
        except AppError as exc:
            result = {"kind": "error", "text": exc.message}
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action="command.executed",
            outcome=Outcome.FAILURE if result["kind"] == "error" else Outcome.SUCCESS,
            resource_type="command",
            resource_id=command.kind.value,
            details={"text": raw[:500]},
        )
        return result

    async def _execute(self, principal: Principal, command: Any) -> dict[str, Any]:
        kind = command.kind
        if kind is CommandKind.INVALID:
            return {"kind": "error", "text": command.error}
        if kind is CommandKind.HELP:
            return {"kind": "result", "text": HELP_TEXT}
        if kind is CommandKind.AGENT_LIST:
            agents = await self._agents.list_agents(principal)
            lines = [
                f"• {a.name} — v{a.latest_version}"
                + (f", đang chạy v{a.published_version}" if a.published_version else ", nháp")
                for a in agents
            ]
            return {"kind": "result", "text": "\n".join(lines) or "Chưa có agent nào."}
        if kind is CommandKind.AGENT_NEW:
            session = await self._architect.create_session(principal)
            return {"kind": "architect", "session_id": str(session.id), "message": command.text}
        if kind is CommandKind.AGENT_EDIT:
            agent = await self._find(principal, command.target)
            session = await self._architect.create_session(principal, agent.id)
            return {"kind": "architect", "session_id": str(session.id), "message": command.text}
        if kind is CommandKind.AGENT_CHAT:
            agent = await self._find(principal, command.target)
            return {"kind": "navigate", "url": f"/agents/{agent.id}/chat"}
        if kind in (CommandKind.AGENT_PUBLISH, CommandKind.AGENT_ROLLBACK):
            agent = await self._find(principal, command.target)
            version = command.version or agent.latest_version
            updated = await self._agents.publish(principal, agent.id, version)
            verb = "Đã publish" if kind is CommandKind.AGENT_PUBLISH else "Đã rollback về"
            return {"kind": "result", "text": f"{verb} “{updated.name}” v{version}."}
        return {"kind": "error", "text": "Lệnh không hỗ trợ."}
