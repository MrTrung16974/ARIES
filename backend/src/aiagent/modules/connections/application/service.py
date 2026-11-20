"""Quản lý connection + Credential Broker (ADR-0016).

Quyền:
- Tạo: Builder trở lên. Scope `workspace` chỉ Admin/Owner.
- Dùng (gắn vào agent, chạy tool): connection `active` và (workspace hoặc chính chủ).
- Quản lý (sửa, test, xóa): chính chủ của connection personal, hoặc Admin/Owner.
- Secret không bao giờ rời service này trừ `open()` (cho MỘT lần gọi tool).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.connections.domain import (
    Connection,
    ConnectionRepositoryPort,
    ConnectionScope,
    ConnectionStatus,
    ConnectorLookupPort,
    SecretVaultPort,
    secret_path,
)
from aiagent.modules.identity.domain import Principal, Role
from aiagent.plugin_sdk import ConnectionContext, PluginKind, PluginManifest
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import (
    AppError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[\w][\w .\-()]{0,79}$", re.UNICODE)
MAX_SECRET_CHARS = 8192
TEST_TIMEOUT_S = 15.0


class ConnectionService:
    def __init__(
        self,
        repo: ConnectionRepositoryPort,
        vault: SecretVaultPort,
        connectors: ConnectorLookupPort,
        audit: AuditService,
    ) -> None:
        self._repo = repo
        self._vault = vault
        self._connectors = connectors
        self._audit = audit

    # ------------------------------------------------------------------ quyền
    @staticmethod
    def can_use(principal: Principal, c: Connection) -> bool:
        return c.status is ConnectionStatus.ACTIVE and (
            c.scope is ConnectionScope.WORKSPACE or c.owner_id == principal.subject
        )

    @staticmethod
    def can_manage(principal: Principal, c: Connection) -> bool:
        if c.status is ConnectionStatus.DELETED:
            return False
        if principal.has_any_role(Role.ADMIN):
            return True
        return c.scope is ConnectionScope.PERSONAL and c.owner_id == principal.subject

    def _visible(self, principal: Principal, c: Connection) -> bool:
        return self.can_manage(principal, c) or self.can_use(principal, c)

    # ------------------------------------------------------------------ connector
    def connectors(self) -> list[PluginManifest]:
        return sorted(self._connectors.connectors(), key=lambda m: m.name)

    def _manifest(self, connector_id: str) -> PluginManifest:
        plugin = self._connectors.get(connector_id)
        if plugin is None or plugin.manifest.kind is not PluginKind.CONNECTOR:
            raise ValidationError(f"Connector '{connector_id}' không tồn tại hoặc đang tắt.")
        return plugin.manifest

    @staticmethod
    def _check_config(manifest: PluginManifest, config: dict[str, Any]) -> dict[str, Any]:
        leaked = set(config) & set(manifest.secrets)
        if leaked:
            raise ValidationError(
                f"Không đặt secret trong config: {', '.join(sorted(leaked))} (dùng 'secrets')."
            )
        schema = manifest.config_schema or {"type": "object"}
        try:
            validator = Draft202012Validator(schema)
        except SchemaError as exc:  # lỗi của plugin, không phải người dùng
            raise AppError("Connector có config_schema không hợp lệ.") from exc
        errors = sorted(validator.iter_errors(config), key=lambda e: list(e.path))
        if errors:
            detail = "; ".join(
                f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in errors[:5]
            )
            raise ValidationError(f"Cấu hình không hợp lệ: {detail}")
        return config

    @staticmethod
    def _check_secrets(manifest: PluginManifest, secrets: dict[str, str]) -> dict[str, str]:
        unknown = set(secrets) - set(manifest.secrets)
        if unknown:
            raise ValidationError(f"Secret không thuộc connector: {', '.join(sorted(unknown))}")
        for key, value in secrets.items():
            if not isinstance(value, str) or len(value) > MAX_SECRET_CHARS:
                raise ValidationError(f"Secret '{key}' không hợp lệ.")
        return secrets

    @staticmethod
    def _check_name(name: str) -> str:
        name = name.strip()
        if not _NAME_RE.match(name):
            raise ValidationError("Tên kết nối 1–80 ký tự, gồm chữ, số, khoảng trắng, . - ( ) _")
        return name

    # ------------------------------------------------------------------ đọc
    async def list_visible(self, principal: Principal) -> list[Connection]:
        items = await self._repo.list_all(principal.tenant_id)
        return [c for c in items if self._visible(principal, c)]

    async def get(self, principal: Principal, connection_id: UUID) -> Connection:
        c = await self._repo.get(principal.tenant_id, connection_id)
        if c is None or not self._visible(principal, c):
            raise NotFoundError("Không tìm thấy kết nối.")
        return c

    async def _managed(self, principal: Principal, connection_id: UUID) -> Connection:
        c = await self.get(principal, connection_id)
        if not self.can_manage(principal, c):
            raise ForbiddenError("Bạn không có quyền quản lý kết nối này.")
        return c

    # ------------------------------------------------------------------ ghi
    async def create(
        self,
        principal: Principal,
        *,
        name: str,
        connector_id: str,
        scope: ConnectionScope,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> Connection:
        if not principal.has_any_role(Role.BUILDER, Role.ADMIN):
            raise ForbiddenError("Cần vai trò Builder để tạo kết nối.")
        if scope is ConnectionScope.WORKSPACE and not principal.has_any_role(Role.ADMIN):
            raise ForbiddenError("Chỉ Admin tạo được kết nối dùng chung (workspace).")
        manifest = self._manifest(connector_id)
        name = self._check_name(name)
        self._check_config(manifest, config)
        secrets = {k: v for k, v in self._check_secrets(manifest, secrets).items() if v}
        missing = set(manifest.secrets) - set(secrets)
        if missing:
            raise ValidationError(f"Thiếu secret: {', '.join(sorted(missing))}")

        now = utcnow()
        connection = Connection(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            name=name,
            connector_id=connector_id,
            scope=scope,
            owner_id=principal.subject,
            config=config,
            secret_keys=sorted(secrets),
            status=ConnectionStatus.ACTIVE,
            last_test_ok=None,
            last_test_note=None,
            last_test_at=None,
            created_at=now,
            updated_at=now,
        )
        path = secret_path(principal.tenant_id, connection.id)
        if secrets:
            await self._vault.put(path, secrets)
        try:
            created = await self._repo.create(connection)
        except Exception:
            if secrets:
                await self._safe_delete(path)
            raise
        await self._record(principal, "connection.created", created)
        return created

    async def update(
        self,
        principal: Principal,
        connection_id: UUID,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
        enabled: bool | None = None,
    ) -> Connection:
        c = await self._managed(principal, connection_id)
        manifest = self._manifest(c.connector_id)
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = self._check_name(name)
        if config is not None:
            changes["config"] = self._check_config(manifest, config)
        if secrets:
            # Chỉ ghi đè key có giá trị mới; key bỏ trống giữ nguyên giá trị cũ.
            fresh = {k: v for k, v in self._check_secrets(manifest, secrets).items() if v}
            if fresh:
                path = secret_path(c.tenant_id, c.id)
                merged = {**await self._vault.get(path), **fresh}
                await self._vault.put(path, merged)
                changes["secret_keys"] = sorted(merged)
        if enabled is not None:
            changes["status"] = ConnectionStatus.ACTIVE if enabled else ConnectionStatus.DISABLED
        if not changes:
            return c
        saved = await self._repo.save(replace(c, **changes, updated_at=utcnow()))
        await self._record(principal, "connection.updated", saved, {"fields": sorted(changes)})
        return saved

    async def delete(self, principal: Principal, connection_id: UUID) -> None:
        c = await self._managed(principal, connection_id)
        await self._repo.save(
            replace(c, status=ConnectionStatus.DELETED, secret_keys=[], updated_at=utcnow())
        )
        await self._safe_delete(secret_path(c.tenant_id, c.id))
        await self._record(principal, "connection.deleted", c)

    async def test(self, principal: Principal, connection_id: UUID) -> Connection:
        c = await self._managed(principal, connection_id)
        plugin = self._connectors.get(c.connector_id)
        if plugin is None:
            raise ValidationError(f"Connector '{c.connector_id}' đang tắt.")
        ctx = await self._context(c)
        try:
            async with asyncio.timeout(TEST_TIMEOUT_S):
                status = await plugin.test_connection(ctx)
            ok, note = status.healthy, status.detail
        except TimeoutError:
            ok, note = False, f"Quá thời gian ({TEST_TIMEOUT_S:.0f}s)."
        except Exception:
            logger.exception("Test connection %s lỗi", c.id)
            ok, note = False, "Connector gặp lỗi nội bộ khi kiểm tra."
        saved = await self._repo.save(
            replace(c, last_test_ok=ok, last_test_note=note[:500], last_test_at=utcnow())
        )
        await self._record(
            principal,
            "connection.tested",
            saved,
            {"ok": ok},
            outcome=Outcome.SUCCESS if ok else Outcome.FAILURE,
        )
        return saved

    # ------------------------------------------------------------------ broker
    async def usable(self, principal: Principal, connection_ids: list[str]) -> list[Connection]:
        out = []
        for raw in connection_ids:
            try:
                cid = UUID(raw)
            except ValueError:
                continue
            c = await self._repo.get(principal.tenant_id, cid)
            if c is not None and self.can_use(principal, c):
                out.append(c)
        return out

    async def open(self, principal: Principal, connection_id: str) -> ConnectionContext:
        """Giải mã secret cho MỘT lần gọi tool; kiểm lại quyền mỗi lần."""
        c = await self._repo.get(principal.tenant_id, UUID(connection_id))
        if c is None or not self.can_use(principal, c):
            raise ForbiddenError("Kết nối không còn khả dụng với bạn.")
        return await self._context(c)

    async def _context(self, c: Connection) -> ConnectionContext:
        secrets = await self._vault.get(secret_path(c.tenant_id, c.id)) if c.secret_keys else {}
        if set(c.secret_keys) - set(secrets):
            # OpenBao chế độ dev lưu trong RAM: khởi động lại là mất secret.
            raise AppError(
                f"Secret của kết nối '{c.name}' không còn trong kho bí mật — hãy nhập lại."
            )
        return ConnectionContext(
            id=str(c.id),
            name=c.name,
            connector_id=c.connector_id,
            config=dict(c.config),
            secrets=secrets,
        )

    # ------------------------------------------------------------------ tiện ích
    async def _safe_delete(self, path: str) -> None:
        try:
            await self._vault.delete(path)
        except Exception:
            logger.exception("Không xóa được secret tại kho bí mật")

    async def _record(
        self,
        principal: Principal,
        action: str,
        c: Connection,
        extra: dict[str, Any] | None = None,
        outcome: Outcome = Outcome.SUCCESS,
    ) -> None:
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action=action,
            outcome=outcome,
            resource_type="connection",
            resource_id=str(c.id),
            details={
                "name": c.name,
                "connector_id": c.connector_id,
                "scope": c.scope.value,
                **(extra or {}),
            },
        )
