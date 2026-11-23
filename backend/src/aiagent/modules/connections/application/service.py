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
import time
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError as PydanticValidationError

from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.connections.domain import (
    RISK_ORDER,
    Connection,
    ConnectionRepositoryPort,
    ConnectionScope,
    ConnectionStatus,
    ConnectorLookupPort,
    OAuthClientPort,
    OAuthProvider,
    SecretVaultPort,
    TokenSet,
    effective_tools,
    secret_path,
)
from aiagent.modules.identity.domain import Principal, Role
from aiagent.plugin_sdk import (
    BlockedEndpointError,
    ConnectionContext,
    NetworkPolicy,
    Plugin,
    PluginKind,
    PluginManifest,
    RiskLevel,
    ToolSpec,
    TriggerSpec,
)
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import (
    AppError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

_TOKEN_MARGIN_S = 60
# Khóa cấu hình do OAuth broker đặt — người dùng không được sửa qua PATCH config (ADR-0018).
_OAUTH_CONFIG_KEYS = ("oauth_provider", "account", "userinfo_url")
_NAME_RE = re.compile(r"^[\w][\w .\-()]{0,79}$", re.UNICODE)
MAX_SECRET_CHARS = 8192
TEST_TIMEOUT_S = 15.0
DISCOVER_TIMEOUT_S = 30.0
MAX_DYNAMIC_TOOLS = 200


class ConnectionService:
    def __init__(
        self,
        repo: ConnectionRepositoryPort,
        vault: SecretVaultPort,
        connectors: ConnectorLookupPort,
        audit: AuditService,
        *,
        oauth: OAuthClientPort | None = None,
        providers: dict[str, OAuthProvider] | None = None,
        network: NetworkPolicy | None = None,
    ) -> None:
        self._repo = repo
        self._vault = vault
        self._connectors = connectors
        self._audit = audit
        self._oauth = oauth
        self.providers: dict[str, OAuthProvider] = dict(providers or {})
        # Mỗi connection một khóa: hai tool call đồng thời không cùng đổi refresh token.
        self._refresh_locks: dict[UUID, asyncio.Lock] = {}
        # None = không kiểm đích mạng (chỉ dùng trong test); container luôn truyền chính sách.
        self._network = network

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

    def manifest_of(self, connector_id: str) -> PluginManifest:
        plugin = self._connectors.get(connector_id)
        if plugin is None or plugin.manifest.kind is not PluginKind.CONNECTOR:
            raise ValidationError(f"Connector '{connector_id}' không tồn tại hoặc đang tắt.")
        return plugin.manifest

    def trigger_of(self, connector_id: str) -> TriggerSpec | None:
        """Trigger (sự kiện mới) của connector, nếu có — dùng cho lịch chạy tự động."""
        plugin = self._connectors.get(connector_id)
        if plugin is None or plugin.manifest.kind is not PluginKind.CONNECTOR:
            return None
        triggers = plugin.manifest.triggers
        return triggers[0] if triggers else None

    def tools_of(self, c: Connection) -> list[ToolSpec]:
        """Tool hiệu lực của connection (tĩnh + động); [] nếu connector đang tắt."""
        plugin = self._connectors.get(c.connector_id)
        if plugin is None or plugin.manifest.kind is not PluginKind.CONNECTOR:
            return []
        return effective_tools(plugin.manifest, c)

    def _check_targets_syntax(self, connector_id: str, config: dict[str, Any]) -> None:
        """Lúc lưu cấu hình: chặn sớm đích mạng rõ ràng bị cấm (không phân giải DNS)."""
        plugin = self._connectors.get(connector_id)
        if self._network is None or plugin is None:
            return
        try:
            for target in plugin.network_targets(config):
                self._network.check_syntax(target)
        except BlockedEndpointError as exc:
            raise ValidationError(str(exc)) from exc

    async def _check_targets(self, plugin: Plugin | None, c: Connection) -> None:
        """Trước mỗi lần dùng: kiểm đầy đủ (kể cả IP phân giải ra)."""
        if self._network is None or plugin is None:
            return
        for target in plugin.network_targets(c.config):
            await self._network.check(target)

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
    def check_name(name: str) -> str:
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
        manifest = self.manifest_of(connector_id)
        if manifest.oauth_provider:
            raise ValidationError("Connector này kết nối bằng OAuth — dùng nút “Kết nối với …”.")
        name = self.check_name(name)
        self._check_config(manifest, config)
        self._check_targets_syntax(connector_id, config)
        secrets = {k: v for k, v in self._check_secrets(manifest, secrets).items() if v}
        missing = set(manifest.secrets) - set(manifest.optional_secrets) - set(secrets)
        if missing:
            raise ValidationError(f"Thiếu secret: {', '.join(sorted(missing))}")
        return await self._store(principal, name, connector_id, scope, config, secrets)

    async def create_oauth(
        self,
        principal: Principal,
        *,
        name: str,
        connector_id: str,
        scope: ConnectionScope,
        provider: OAuthProvider,
        account: str,
        tokens: TokenSet,
    ) -> Connection:
        """Do OAuth broker gọi sau khi đổi code lấy token (ADR-0018)."""
        self.check_create_rights(principal, scope)
        manifest = self.manifest_of(connector_id)
        if manifest.oauth_provider != provider.id:
            raise ValidationError("Connector không dùng nhà cung cấp OAuth này.")
        secrets = {
            "access_token": tokens.access_token,
            "expires_at": str(int(tokens.expires_at)),
        }
        if tokens.refresh_token:
            secrets["refresh_token"] = tokens.refresh_token
        config: dict[str, Any] = {"oauth_provider": provider.id, "account": account[:200]}
        if provider.userinfo_url:
            config["userinfo_url"] = provider.userinfo_url
        return await self._store(
            principal, self.check_name(name), connector_id, scope, config, secrets
        )

    @staticmethod
    def check_create_rights(principal: Principal, scope: ConnectionScope) -> None:
        if not principal.has_any_role(Role.BUILDER, Role.ADMIN):
            raise ForbiddenError("Cần vai trò Builder để tạo kết nối.")
        if scope is ConnectionScope.WORKSPACE and not principal.has_any_role(Role.ADMIN):
            raise ForbiddenError("Chỉ Admin tạo được kết nối dùng chung (workspace).")

    async def _store(
        self,
        principal: Principal,
        name: str,
        connector_id: str,
        scope: ConnectionScope,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> Connection:
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
        risk_overrides: dict[str, str] | None = None,
    ) -> Connection:
        c = await self._managed(principal, connection_id)
        manifest = self.manifest_of(c.connector_id)
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = self.check_name(name)
        if config is not None:
            if manifest.oauth_provider:
                # Giữ nguyên khóa OAuth: xóa/sửa chúng làm lộ refresh token cho plugin hoặc
                # gửi access token tới URL khác.
                config = {k: v for k, v in config.items() if k not in _OAUTH_CONFIG_KEYS}
                config |= {k: c.config[k] for k in _OAUTH_CONFIG_KEYS if k in c.config}
            changes["config"] = self._check_config(manifest, config)
            self._check_targets_syntax(c.connector_id, config)
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
        if risk_overrides is not None:
            changes["risk_overrides"] = self._check_overrides(principal, c, risk_overrides)
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
        tool_changes: dict[str, Any] = {}
        try:
            ctx = await self._context(c)
        except BlockedEndpointError as exc:
            ok, note = False, f"Đích mạng bị chặn: {exc}"
        else:
            try:
                async with asyncio.timeout(TEST_TIMEOUT_S):
                    status = await plugin.test_connection(ctx)
                ok, note = status.healthy, status.detail
            except TimeoutError:
                ok, note = False, f"Quá thời gian ({TEST_TIMEOUT_S:.0f}s)."
            except Exception:
                logger.exception("Test connection %s lỗi", c.id)
                ok, note = False, "Connector gặp lỗi nội bộ khi kiểm tra."
            if ok and plugin.manifest.dynamic_tools:
                # Kết nối tốt → làm mới luôn danh sách tool (UI tự test sau khi lưu).
                try:
                    tool_changes = self._tool_changes(c, await self._discover(plugin, ctx))
                    note = f"{note} Đã đồng bộ {len(tool_changes['tools'])} tool.".strip()
                except AppError as exc:
                    note = f"{note} Không đồng bộ được tool: {exc.message}".strip()
        saved = await self._repo.save(
            replace(
                c,
                **tool_changes,
                last_test_ok=ok,
                last_test_note=note[:500],
                last_test_at=utcnow(),
            )
        )
        await self._record(
            principal,
            "connection.tested",
            saved,
            {"ok": ok},
            outcome=Outcome.SUCCESS if ok else Outcome.FAILURE,
        )
        return saved

    # ------------------------------------------------------------------ tool động (ADR-0023)
    async def sync_tools(self, principal: Principal, connection_id: UUID) -> Connection:
        """Gọi connector khám phá lại tool của connection (OpenAPI, gRPC reflection…)."""
        c = await self._managed(principal, connection_id)
        plugin = self._connectors.get(c.connector_id)
        if plugin is None:
            raise ValidationError(f"Connector '{c.connector_id}' đang tắt.")
        if not plugin.manifest.dynamic_tools:
            raise ValidationError("Connector này có bộ tool cố định — không cần đồng bộ.")
        try:
            ctx = await self._context(c)
        except BlockedEndpointError as exc:
            raise ValidationError(f"Đích mạng bị chặn: {exc}") from exc
        changes = self._tool_changes(c, await self._discover(plugin, ctx))
        saved = await self._repo.save(replace(c, **changes, updated_at=utcnow()))
        await self._record(principal, "connection.tools_synced", saved, {"tools": len(saved.tools)})
        return saved

    async def _discover(self, plugin: Plugin, ctx: ConnectionContext) -> tuple[ToolSpec, ...]:
        try:
            async with asyncio.timeout(DISCOVER_TIMEOUT_S):
                raw = await plugin.discover_tools(ctx)
        except TimeoutError as exc:
            raise AppError(f"Khám phá tool quá thời gian ({DISCOVER_TIMEOUT_S:.0f}s).") from exc
        except (ConnectionError, OSError, BlockedEndpointError) as exc:
            raise AppError(str(exc)[:300] or "Không kết nối được để khám phá tool.") from exc
        except AppError:
            raise
        except Exception as exc:
            logger.exception("Connector %s lỗi khi khám phá tool", plugin.manifest.id)
            raise AppError("Connector gặp lỗi nội bộ khi khám phá tool.") from exc
        return self._sanitize_tools(plugin.manifest, raw)

    @staticmethod
    def _sanitize_tools(manifest: PluginManifest, raw: list[ToolSpec]) -> tuple[ToolSpec, ...]:
        """Tool động từ hệ thống ngoài: bắt buộc tiền tố, không trùng tool tĩnh, schema hợp lệ,
        output luôn không tin cậy, giới hạn số lượng."""
        prefix = f"{manifest.id}."
        seen = {t.name for t in manifest.tools}
        out: list[ToolSpec] = []
        for item in raw:
            try:
                tool = item if isinstance(item, ToolSpec) else ToolSpec.model_validate(item)
                Draft202012Validator.check_schema(tool.input_schema)
            except (PydanticValidationError, SchemaError):
                logger.warning("Bỏ tool động không hợp lệ của connector %s", manifest.id)
                continue
            if not tool.name.startswith(prefix) or tool.name in seen:
                logger.warning("Bỏ tool động %s (sai tiền tố hoặc trùng tên)", tool.name)
                continue
            seen.add(tool.name)
            out.append(tool.model_copy(update={"untrusted_output": True}))
            if len(out) >= MAX_DYNAMIC_TOOLS:
                logger.warning("Connector %s trả quá %d tool — cắt bớt", manifest.id, len(raw))
                break
        return tuple(out)

    @staticmethod
    def _tool_changes(c: Connection, tools: tuple[ToolSpec, ...]) -> dict[str, Any]:
        names = {t.name for t in tools}
        return {
            "tools": tools,
            # Bỏ ghi đè của tool không còn tồn tại.
            "risk_overrides": {k: v for k, v in c.risk_overrides.items() if k in names},
            "tools_synced_at": utcnow(),
        }

    @staticmethod
    def _check_overrides(
        principal: Principal, c: Connection, overrides: dict[str, str]
    ) -> dict[str, str]:
        """Ghi đè rủi ro tool động. Chỉ Admin được HẠ mức rủi ro (bớt bước duyệt)."""
        known = {t.name: t for t in c.tools}
        out: dict[str, str] = {}
        for name, value in overrides.items():
            tool = known.get(name)
            if tool is None:
                raise ValidationError(f"Kết nối không có tool động '{name}'.")
            try:
                risk = RiskLevel(value)
            except ValueError as exc:
                raise ValidationError(f"Mức rủi ro không hợp lệ: {value}") from exc
            if RISK_ORDER[risk] < RISK_ORDER[tool.risk] and not principal.has_any_role(Role.ADMIN):
                raise ForbiddenError("Chỉ Admin được hạ mức rủi ro của tool.")
            if risk is not tool.risk:
                out[name] = risk.value
        return out

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
        try:
            return await self._context(c)
        except BlockedEndpointError as exc:
            raise ForbiddenError(f"Kết nối '{c.name}' trỏ tới đích mạng bị chặn: {exc}") from exc

    async def _context(self, c: Connection) -> ConnectionContext:
        """BlockedEndpointError nếu cấu hình trỏ tới đích mạng bị cấm."""
        await self._check_targets(self._connectors.get(c.connector_id), c)
        secrets = await self._vault.get(secret_path(c.tenant_id, c.id)) if c.secret_keys else {}
        if set(c.secret_keys) - set(secrets):
            # OpenBao chế độ dev lưu trong RAM: khởi động lại là mất secret.
            raise AppError(
                f"Secret của kết nối '{c.name}' không còn trong kho bí mật — hãy nhập lại."
            )
        provider_id = c.config.get("oauth_provider")
        if provider_id:
            secrets = {"access_token": await self._fresh_access_token(c, str(provider_id), secrets)}
        return ConnectionContext(
            id=str(c.id),
            name=c.name,
            connector_id=c.connector_id,
            config=dict(c.config),
            secrets=secrets,
            network=self._network,
        )

    async def _fresh_access_token(
        self, c: Connection, provider_id: str, secrets: dict[str, str]
    ) -> str:
        """Access token còn hạn; hết hạn thì dùng refresh token đổi mới và lưu lại. Plugin không
        bao giờ nhận refresh token."""
        expires_at = float(secrets.get("expires_at") or 0)
        if secrets.get("access_token") and expires_at - time.time() > _TOKEN_MARGIN_S:
            return secrets["access_token"]
        provider = self.providers.get(provider_id)
        refresh = secrets.get("refresh_token")
        if provider is None or self._oauth is None or not refresh:
            raise AppError(f"Kết nối '{c.name}' đã hết hạn — hãy kết nối lại với {provider_id}.")
        path = secret_path(c.tenant_id, c.id)
        async with self._refresh_locks.setdefault(c.id, asyncio.Lock()):
            # Lượt chờ khóa: có thể lượt trước đã làm mới xong → dùng luôn, không đổi lần nữa
            # (nhà cung cấp xoay vòng refresh token sẽ từ chối token cũ).
            latest = await self._vault.get(path)
            if (
                latest.get("access_token")
                and float(latest.get("expires_at") or 0) - time.time() > _TOKEN_MARGIN_S
            ):
                return latest["access_token"]
            refresh = latest.get("refresh_token") or refresh
            tokens = await self._oauth.refresh(provider, refresh)
            fresh = {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token or refresh,
                "expires_at": str(int(tokens.expires_at)),
            }
            await self._vault.put(path, fresh)
        return tokens.access_token

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
