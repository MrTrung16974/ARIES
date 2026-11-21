"""Quản lý kết nối nhà cung cấp LLM (Gemini, Ollama, OpenAI, Claude, Groq, OpenRouter, DeepSeek).

Lưu cấu hình (base_url, models, extra, enabled) ở PostgreSQL (llm.providers) và API key ở
OpenBao (aries/{tenant_id}/llm/{provider_id}). Cập nhật nóng ModelRouter & ModelCatalog
ngay khi lưu, không cần khởi động lại server.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.connections.domain import SecretVaultPort
from aiagent.modules.identity.domain import Principal, Role
from aiagent.modules.runtime.application.model_catalog import ModelCatalog
from aiagent.modules.runtime.application.router import ModelRouter
from aiagent.modules.runtime.domain import (
    PROVIDER_BY_ID,
    PROVIDER_CATALOG,
    ChatModelPort,
    ModelRateLimitedError,
    ModelRequest,
    ProbeResult,
    ProviderDescriptor,
    ProviderFactoryPort,
    ProviderRecord,
    ProviderRepositoryPort,
)
from aiagent.shared_kernel.errors import (
    AppError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

ConfigSource = Literal["db", "env", "none"]
MAX_MODELS_TO_CHECK = 100
CHECK_TIMEOUT_S = 30


@dataclass(frozen=True)
class ModelCheck:
    model: str
    ok: bool
    error: str | None
    latency_ms: int | None = None


@dataclass(frozen=True)
class ProviderView:
    descriptor: ProviderDescriptor
    configured: bool
    enabled: bool
    source: ConfigSource
    base_url: str
    has_api_key: bool
    custom_models: list[str] = field(default_factory=list)
    available_models: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    updated_by: str | None = None
    updated_at: datetime | None = None


def _vault_path(tenant_id: str, provider_id: str) -> str:
    # Chính sách AppRole chỉ cho phép secret/data/aries/* (deploy/openbao/init.sh).
    return f"aries/{tenant_id}/llm/{provider_id}"


def _clean_models(models: list[str] | None) -> list[str]:
    if not models:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in models:
        m = raw.strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


class ModelProviderService:
    def __init__(
        self,
        repo: ProviderRepositoryPort,
        vault: SecretVaultPort,
        factory: ProviderFactoryPort,
        router: ModelRouter,
        catalog: ModelCatalog,
        audit: AuditService,
        *,
        env_providers: dict[str, ChatModelPort] | None = None,
        env_allowed: dict[str, frozenset[str] | None] | None = None,
        env_base_urls: dict[str, str] | None = None,
        env_api_keys: dict[str, str] | None = None,
        list_tenants: Callable[[], Awaitable[list[str]]] | None = None,
    ) -> None:
        self._repo = repo
        self._vault = vault
        self._factory = factory
        self._router = router
        self._catalog = catalog
        self._audit = audit
        self._env_providers = dict(env_providers or {})
        self._env_allowed = dict(env_allowed or {})
        self._env_base_urls = dict(env_base_urls or {})
        self._env_api_keys = dict(env_api_keys or {})
        self._list_tenants = list_tenants

    # ------------------------------------------------------------------ startup hydrate
    async def hydrate_all(self) -> None:
        """Nạp lại các provider đã lưu trong DB khi khởi động (không làm sập app nếu bảng chưa có)."""
        if self._list_tenants is None:
            return
        try:
            tenants = await self._list_tenants()
        except Exception as exc:
            logger.warning("Không đọc được danh sách tenant để nạp LLM provider: %s", exc)
            return
        for tid in tenants:
            try:
                records = await self._repo.list_for_tenant(tid)
            except Exception as exc:
                logger.warning("Không đọc được llm.providers cho tenant %s: %s", tid, exc)
                continue
            for rec in records:
                if rec.provider_id not in PROVIDER_BY_ID or not rec.enabled:
                    continue
                await self._activate_record(rec)

    async def _activate_record(self, rec: ProviderRecord) -> None:
        api_key: str | None = None
        if rec.has_api_key:
            try:
                secret = await self._vault.get(_vault_path(rec.tenant_id, rec.provider_id))
                api_key = secret.get("api_key")
            except Exception as exc:
                logger.warning("Không đọc được API key cho provider %s: %s", rec.provider_id, exc)
                return
        model = self._factory.build(
            rec.provider_id,
            base_url=rec.base_url,
            api_key=api_key,
            extra=rec.extra,
        )
        allowed = self._allowed_for(rec.provider_id, rec.models)
        self._router.set_provider(rec.provider_id, model)
        self._catalog.set_provider(rec.provider_id, model, allowed)

    def _allowed_for(self, provider_id: str, custom_models: list[str]) -> frozenset[str] | None:
        if custom_models:
            return frozenset(custom_models)
        if provider_id == "anthropic":
            return self._env_allowed.get("anthropic") or frozenset(
                PROVIDER_BY_ID["anthropic"].sample_models
            )
        return None

    # ------------------------------------------------------------------ queries
    async def list_providers(self, principal: Principal) -> list[ProviderView]:
        if not principal.has_any_role(Role.ADMIN, Role.BUILDER):
            raise ForbiddenError("Chỉ Admin hoặc Builder được xem cấu hình nhà cung cấp LLM.")
        try:
            records = await self._repo.list_for_tenant(principal.tenant_id)
        except Exception as exc:
            logger.warning(
                "Không đọc được llm.providers (có thể chưa chạy migration 0011): %s", exc
            )
            records = []
        by_id = {r.provider_id: r for r in records}

        out: list[ProviderView] = []
        for desc in PROVIDER_CATALOG:
            rec = by_id.get(desc.id)
            in_env = desc.id in self._env_providers
            active = self._catalog.has_provider(desc.id)
            models = await self._catalog.models_of(desc.id) if active else []

            if rec is not None:
                source: ConfigSource = "db"
                configured = rec.enabled and active
                enabled = rec.enabled
                base_url = rec.base_url or desc.default_base_url
                has_key = rec.has_api_key
                custom_models = list(rec.models)
                extra = dict(rec.extra)
                updated_by = rec.updated_by
                updated_at = rec.updated_at
            elif in_env:
                source = "env"
                configured = True
                enabled = True
                base_url = self._env_base_urls.get(desc.id, desc.default_base_url)
                has_key = desc.requires_api_key
                env_m = self._env_allowed.get(desc.id)
                custom_models = sorted(env_m) if env_m is not None else []
                extra = {}
                updated_by = "env"
                updated_at = None
            else:
                source = "none"
                configured = False
                enabled = False
                base_url = desc.default_base_url
                has_key = False
                custom_models = []
                extra = {"free_only": True} if desc.supports_free_only else {}
                updated_by = None
                updated_at = None

            out.append(
                ProviderView(
                    descriptor=desc,
                    configured=configured,
                    enabled=enabled,
                    source=source,
                    base_url=base_url,
                    has_api_key=has_key,
                    custom_models=custom_models,
                    available_models=models,
                    extra=extra,
                    updated_by=updated_by,
                    updated_at=updated_at,
                )
            )
        return out

    # ------------------------------------------------------------------ commands
    async def configure(
        self,
        principal: Principal,
        provider_id: str,
        *,
        enabled: bool = True,
        base_url: str | None = None,
        api_key: str | None = None,
        models: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ProviderView:
        self._require_admin(principal)
        desc = self._require_descriptor(provider_id)
        existing = await self._repo.get(principal.tenant_id, provider_id)

        clean_url = (base_url or "").strip() or desc.default_base_url
        if desc.requires_base_url and not clean_url:
            raise ValidationError(f"{desc.name} bắt buộc phải có Base URL.")

        raw_key = (api_key or "").strip()
        effective_key: str | None = None
        has_api_key = False

        if raw_key:
            await self._vault.put(
                _vault_path(principal.tenant_id, provider_id), {"api_key": raw_key}
            )
            effective_key = raw_key
            has_api_key = True
        elif existing is not None and existing.has_api_key:
            stored = await self._vault.get(_vault_path(principal.tenant_id, provider_id))
            effective_key = stored.get("api_key")
            has_api_key = bool(effective_key)
        elif desc.id in self._env_api_keys:
            effective_key = self._env_api_keys[desc.id]
            if effective_key:
                await self._vault.put(
                    _vault_path(principal.tenant_id, provider_id), {"api_key": effective_key}
                )
                has_api_key = True

        if enabled and desc.requires_api_key and not effective_key:
            raise ValidationError(f"Vui lòng nhập API key cho {desc.name}.")

        clean_models = _clean_models(models)
        clean_extra = dict(extra or {})

        rec = await self._repo.upsert(
            principal.tenant_id,
            provider_id,
            enabled=enabled,
            base_url=clean_url,
            models=clean_models,
            extra=clean_extra,
            has_api_key=has_api_key,
            updated_by=principal.subject,
        )

        if enabled:
            model = self._factory.build(
                provider_id,
                base_url=rec.base_url,
                api_key=effective_key,
                extra=rec.extra,
            )
            allowed = self._allowed_for(provider_id, rec.models)
            self._router.set_provider(provider_id, model)
            self._catalog.set_provider(provider_id, model, allowed)
        else:
            self._router.remove_provider(provider_id)
            self._catalog.remove_provider(provider_id)

        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action="llm_provider.configured",
            outcome=Outcome.SUCCESS,
            resource_type="llm_provider",
            resource_id=provider_id,
            details={"enabled": enabled, "base_url": clean_url, "custom_models": len(clean_models)},
        )

        views = await self.list_providers(principal)
        return next(v for v in views if v.descriptor.id == provider_id)

    async def check_models(
        self,
        principal: Principal,
        provider_id: str,
        models: list[str],
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> list[ModelCheck]:
        """Gọi thử từng model một yêu cầu rất ngắn — phát hiện model có trong /models nhưng
        không dùng được (ngừng cung cấp, hết quota, quá tải…)."""
        self._require_admin(principal)
        wanted = _clean_models(models)
        if not wanted:
            raise ValidationError("Chưa chọn model nào để kiểm tra.")
        if len(wanted) > MAX_MODELS_TO_CHECK:
            raise ValidationError(f"Mỗi lần kiểm tra tối đa {MAX_MODELS_TO_CHECK} model.")
        url, key, ext = await self._effective(principal, provider_id, base_url, api_key, extra)
        chat = self._factory.build(provider_id, base_url=url, api_key=key, extra=ext)
        limit = asyncio.Semaphore(3)

        async def one(model: str) -> ModelCheck:
            request = ModelRequest(
                model=model,
                system="Trả lời đúng một từ.",
                messages=[{"role": "user", "content": [{"type": "text", "text": "Nói: OK"}]}],
                max_tokens=16,
                effort="low",
            )
            async with limit:
                started = time.monotonic()
                try:
                    async with asyncio.timeout(CHECK_TIMEOUT_S):
                        async for _event in chat.stream(request):
                            break  # có phản hồi đầu tiên là đủ
                except ModelRateLimitedError:
                    return ModelCheck(model, False, "Hết quota / đang bị giới hạn tần suất (429).")
                except AppError as exc:
                    return ModelCheck(model, False, exc.message)
                except TimeoutError:
                    return ModelCheck(model, False, f"Không phản hồi sau {CHECK_TIMEOUT_S}s.")
                except Exception as exc:  # lỗi lạ của SDK — không làm hỏng cả lượt kiểm tra
                    return ModelCheck(model, False, f"Lỗi: {type(exc).__name__}")
                return ModelCheck(model, True, None, int((time.monotonic() - started) * 1000))

        return list(await asyncio.gather(*(one(m) for m in wanted)))

    async def _effective(
        self,
        principal: Principal,
        provider_id: str,
        base_url: str | None,
        api_key: str | None,
        extra: dict[str, Any] | None,
    ) -> tuple[str, str | None, dict[str, Any]]:
        """URL/key/extra dùng để kết nối: giá trị đang nhập → đã lưu (DB/OpenBao) → .env → mặc định."""
        desc = self._require_descriptor(provider_id)
        existing = await self._repo.get(principal.tenant_id, provider_id)
        url = (
            (base_url or "").strip()
            or (existing.base_url if existing and existing.base_url else None)
            or self._env_base_urls.get(provider_id)
            or desc.default_base_url
        )
        key = (api_key or "").strip() or None
        if not key and existing is not None and existing.has_api_key:
            try:
                stored = await self._vault.get(_vault_path(principal.tenant_id, provider_id))
                key = stored.get("api_key")
            except Exception:
                key = None
        key = key or self._env_api_keys.get(provider_id)
        ext = dict(extra) if extra is not None else (dict(existing.extra) if existing else {})
        return url, key, ext

    async def test_connection(
        self,
        principal: Principal,
        provider_id: str,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ProbeResult:
        self._require_admin(principal)
        desc = self._require_descriptor(provider_id)
        existing = await self._repo.get(principal.tenant_id, provider_id)

        effective_url = (
            (base_url or "").strip()
            or (existing.base_url if existing and existing.base_url else None)
            or self._env_base_urls.get(provider_id)
            or desc.default_base_url
        )
        effective_key = (api_key or "").strip() or None
        if not effective_key and existing is not None and existing.has_api_key:
            try:
                stored = await self._vault.get(_vault_path(principal.tenant_id, provider_id))
                effective_key = stored.get("api_key")
            except Exception:
                effective_key = None
        if not effective_key:
            effective_key = self._env_api_keys.get(provider_id)

        effective_extra = (
            dict(extra) if extra is not None else (dict(existing.extra) if existing else {})
        )
        return await self._factory.probe(
            provider_id,
            base_url=effective_url,
            api_key=effective_key,
            extra=effective_extra,
        )

    async def remove(self, principal: Principal, provider_id: str) -> None:
        self._require_admin(principal)
        self._require_descriptor(provider_id)
        await self._repo.delete(principal.tenant_id, provider_id)
        await self._vault.delete(_vault_path(principal.tenant_id, provider_id))

        # Nếu .env có cấu hình sẵn → khôi phục về .env; không thì tắt khỏi runtime.
        if provider_id in self._env_providers:
            model = self._env_providers[provider_id]
            self._router.set_provider(provider_id, model)
            self._catalog.set_provider(provider_id, model, self._env_allowed.get(provider_id))
        else:
            self._router.remove_provider(provider_id)
            self._catalog.remove_provider(provider_id)

        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action="llm_provider.removed",
            outcome=Outcome.SUCCESS,
            resource_type="llm_provider",
            resource_id=provider_id,
            details={},
        )

    def _require_admin(self, principal: Principal) -> None:
        if not principal.has_any_role(Role.ADMIN):
            raise ForbiddenError("Chỉ Admin mới được cấu hình nhà cung cấp LLM.")

    def _require_descriptor(self, provider_id: str) -> ProviderDescriptor:
        desc = PROVIDER_BY_ID.get(provider_id)
        if desc is None:
            raise NotFoundError(f"Nhà cung cấp LLM '{provider_id}' không tồn tại.")
        return desc
