"""Quản lý MCP server + adapter thành Plugin để ToolGateway dùng như mọi tool khác (ADR-0018).

Quyền: Admin đăng ký/sửa/xóa/đồng bộ; Builder trở lên xem và gắn vào agent; ai chạy agent cũng
dùng được tool (vẫn qua policy/HITL/audit của ToolGateway).
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.connections.domain import SecretVaultPort
from aiagent.modules.identity.domain import Principal, Role
from aiagent.modules.mcp.domain import (
    SLUG_PATTERN,
    McpClientPort,
    McpEndpoint,
    McpServer,
    McpServerRepositoryPort,
    McpTool,
    McpTransport,
    secret_path,
    tool_manifest_name,
)
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
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import AppError, ForbiddenError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

_SECRET_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{0,63}$")


class McpPlugin(Plugin):
    """Một MCP server nhìn như một skill: tool `mcp.<slug>.<tool>`, output luôn không tin cậy."""

    def __init__(self, server: McpServer, service: McpService) -> None:
        self._server = server
        self._service = service
        self._names: dict[str, str] = {}
        tools = []
        for t in server.tools:
            name = tool_manifest_name(server.slug, t.name)
            if name in self._names:
                continue
            self._names[name] = t.name
            tools.append(
                ToolSpec(
                    name=name,
                    description=(t.description or t.name)[:1024],
                    risk=server.effective_risk(t),
                    input_schema=t.input_schema or {"type": "object"},
                    untrusted_output=True,
                )
            )
        self._manifest = PluginManifest(
            id=f"mcp_{server.slug}",
            kind=PluginKind.SKILL,
            name=f"MCP · {server.name}",
            description=f"MCP server {server.name}",
            version="1.0.0",
            api_version=PLUGIN_API_VERSION,
            tools=tuple(tools),
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    async def call_tool(self, name: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        original = self._names.get(name)
        if original is None:
            return ToolResult(f"MCP server không có tool '{name}'.", is_error=True)
        text, is_error = await self._service.call(self._server, original, arguments)
        return ToolResult(text, is_error=is_error)


class McpService:
    def __init__(
        self,
        repo: McpServerRepositoryPort,
        vault: SecretVaultPort,
        client: McpClientPort,
        audit: AuditService,
        stdio_commands: dict[str, list[str]],
    ) -> None:
        self._repo = repo
        self._vault = vault
        self._client = client
        self._audit = audit
        self._commands = stdio_commands

    # ------------------------------------------------------------------ quyền
    @staticmethod
    def require_admin(principal: Principal) -> None:
        if not principal.has_any_role(Role.ADMIN):
            raise ForbiddenError("Chỉ Admin quản lý MCP server.")

    @staticmethod
    def _require_builder(principal: Principal) -> None:
        if not principal.has_any_role(Role.BUILDER, Role.ADMIN):
            raise ForbiddenError("Cần vai trò Builder.")

    def stdio_commands(self) -> list[str]:
        return sorted(self._commands)

    # ------------------------------------------------------------------ đọc
    async def list_servers(self, principal: Principal) -> list[McpServer]:
        self._require_builder(principal)
        return [s for s in await self._repo.list_all(principal.tenant_id) if not s.deleted]

    async def get(self, principal: Principal, server_id: UUID) -> McpServer:
        self._require_builder(principal)
        s = await self._repo.get(principal.tenant_id, server_id)
        if s is None or s.deleted:
            raise NotFoundError("Không tìm thấy MCP server.")
        return s

    # ------------------------------------------------------------------ ghi
    def _check(self, transport: McpTransport, url: str | None, command_key: str | None) -> None:
        if transport is McpTransport.HTTP:
            if not url or not re.match(r"^https?://[^\s/]+", url):
                raise ValidationError("MCP HTTP cần URL http(s) hợp lệ.")
        elif not command_key or command_key not in self._commands:
            raise ValidationError(
                "Lệnh stdio phải nằm trong danh sách cho phép (AIAGENT_MCP_STDIO_COMMANDS)."
            )

    @staticmethod
    def _check_secrets(secrets: dict[str, str]) -> dict[str, str]:
        for key, value in secrets.items():
            if not _SECRET_KEY_RE.match(key) or len(value) > 8192:
                raise ValidationError(f"Secret '{key}' không hợp lệ.")
        return {k: v for k, v in secrets.items() if v}

    async def create(
        self,
        principal: Principal,
        *,
        name: str,
        slug: str,
        transport: McpTransport,
        url: str | None,
        command_key: str | None,
        secrets: dict[str, str],
    ) -> McpServer:
        self.require_admin(principal)
        if not re.match(SLUG_PATTERN, slug):
            raise ValidationError("Mã server: chữ thường, số, gạch dưới; bắt đầu bằng chữ.")
        self._check(transport, url, command_key)
        secrets = self._check_secrets(secrets)
        now = utcnow()
        server = McpServer(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            name=name.strip()[:80] or slug,
            slug=slug,
            transport=transport,
            url=url if transport is McpTransport.HTTP else None,
            command_key=command_key if transport is McpTransport.STDIO else None,
            secret_keys=sorted(secrets),
            enabled=True,
            deleted=False,
            tools=[],
            risk_overrides={},
            last_sync_at=None,
            last_error=None,
            created_by=principal.subject,
            created_at=now,
            updated_at=now,
        )
        if secrets:
            await self._vault.put(secret_path(server.tenant_id, server.id), secrets)
        created = await self._repo.create(server)
        await self._record(principal, "mcp.server_created", created)
        return await self.sync(principal, created.id)

    async def update(
        self,
        principal: Principal,
        server_id: UUID,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        secrets: dict[str, str] | None = None,
        risk_overrides: dict[str, str] | None = None,
    ) -> McpServer:
        self.require_admin(principal)
        s = await self.get(principal, server_id)
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name.strip()[:80] or s.name
        if enabled is not None:
            changes["enabled"] = enabled
        if secrets:
            fresh = self._check_secrets(secrets)
            if fresh:
                path = secret_path(s.tenant_id, s.id)
                merged = {**await self._vault.get(path), **fresh}
                await self._vault.put(path, merged)
                changes["secret_keys"] = sorted(merged)
        if risk_overrides is not None:
            known = {t.name for t in s.tools}
            for tool, risk in risk_overrides.items():
                if tool not in known:
                    raise ValidationError(f"Server không có tool '{tool}'.")
                RiskLevel(risk)  # ValueError nếu sai
            changes["risk_overrides"] = dict(risk_overrides)
        if not changes:
            return s
        saved = await self._repo.save(replace(s, **changes, updated_at=utcnow()))
        await self._record(principal, "mcp.server_updated", saved, {"fields": sorted(changes)})
        return saved

    async def delete(self, principal: Principal, server_id: UUID) -> None:
        self.require_admin(principal)
        s = await self.get(principal, server_id)
        await self._repo.save(replace(s, deleted=True, enabled=False, updated_at=utcnow()))
        try:
            await self._vault.delete(secret_path(s.tenant_id, s.id))
        except AppError:
            logger.warning("Không xóa được secret của MCP server %s", s.id)
        await self._record(principal, "mcp.server_deleted", s)

    async def sync(self, principal: Principal, server_id: UUID) -> McpServer:
        """Lấy lại danh sách tool từ server (tools/list)."""
        self.require_admin(principal)
        s = await self.get(principal, server_id)
        try:
            tools = await self._client.list_tools(await self._endpoint(s))
            saved = await self._repo.save(
                replace(s, tools=tools, last_sync_at=utcnow(), last_error=None, updated_at=utcnow())
            )
            ok = True
        except AppError as exc:
            saved = await self._repo.save(
                replace(s, last_error=exc.message[:500], last_sync_at=utcnow(), updated_at=utcnow())
            )
            ok = False
        await self._record(
            principal,
            "mcp.server_synced",
            saved,
            {"ok": ok, "tools": len(saved.tools)},
            outcome=Outcome.SUCCESS if ok else Outcome.FAILURE,
        )
        return saved

    # ------------------------------------------------------------------ runtime
    async def plugins(
        self, principal: Principal, server_ids: list[str]
    ) -> list[tuple[str, Plugin]]:
        """Adapter Plugin cho các server đang bật trong danh sách (dùng khi bind tool của run)."""
        out: list[tuple[str, Plugin]] = []
        for raw in server_ids:
            try:
                sid = UUID(raw)
            except ValueError:
                continue
            s = await self._repo.get(principal.tenant_id, sid)
            if s is not None and s.enabled and not s.deleted:
                out.append((raw, McpPlugin(s, self)))
        return out

    async def tools_for(self, principal: Principal, server_id: str) -> list[ToolSpec] | None:
        found = await self.plugins(principal, [server_id])
        return list(found[0][1].manifest.tools) if found else None

    async def call(
        self, server: McpServer, tool: str, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
        return await self._client.call_tool(await self._endpoint(server), tool, arguments)

    async def _endpoint(self, s: McpServer) -> McpEndpoint:
        secrets = await self._vault.get(secret_path(s.tenant_id, s.id)) if s.secret_keys else {}
        if s.transport is McpTransport.HTTP:
            return McpEndpoint(McpTransport.HTTP, url=s.url, secrets=secrets)
        command = self._commands.get(s.command_key or "")
        if not command:
            raise AppError("Lệnh stdio của MCP server không còn trong danh sách cho phép.")
        return McpEndpoint(McpTransport.STDIO, command=list(command), secrets=secrets)

    async def _record(
        self,
        principal: Principal,
        action: str,
        s: McpServer,
        extra: dict[str, Any] | None = None,
        outcome: Outcome = Outcome.SUCCESS,
    ) -> None:
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action=action,
            outcome=outcome,
            resource_type="mcp_server",
            resource_id=str(s.id),
            details={"name": s.name, "transport": s.transport.value, **(extra or {})},
        )


__all__ = ["McpPlugin", "McpService", "McpTool"]
