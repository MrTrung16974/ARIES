"""Quản lý & xác thực kênh publish (ADR-0020).

- Tạo/thu hồi: người sửa được agent (chủ agent Builder, hoặc Admin).
- Link công khai: chỉ cho agent có `data_classification = public` (kiểm lúc tạo VÀ lúc chạy).
- Bí mật chỉ hiện một lần; lưu SHA-256; so sánh hằng thời gian.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from aiagent.modules.agents.application import AgentService
from aiagent.modules.agents.domain import Agent, AgentVersion
from aiagent.modules.audit.application import AuditService
from aiagent.modules.audit.domain import Outcome
from aiagent.modules.channels.domain import (
    ApiKey,
    ChannelRepositoryPort,
    RateLimiterPort,
    ShareLink,
    hash_secret,
    new_secret,
    split_token,
)
from aiagent.modules.identity.domain import Principal
from aiagent.shared_kernel.clock import utcnow
from aiagent.shared_kernel.errors import (
    AppError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)

_VISITOR_RE = re.compile(r"^[A-Za-z0-9\-]{16,64}$")
_USER_RE = re.compile(r"^[A-Za-z0-9_.@:\-]{1,80}$")
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")


class RateLimitedError(AppError):
    code = "rate_limited"
    status_code = 429
    public_message = "Quá nhiều yêu cầu — hãy thử lại sau ít phút."


class ChannelService:
    def __init__(
        self,
        repo: ChannelRepositoryPort,
        agents: AgentService,
        limiter: RateLimiterPort,
        audit: AuditService,
    ) -> None:
        self._repo = repo
        self._agents = agents
        self._limiter = limiter
        self._audit = audit

    # ------------------------------------------------------------------ quyền
    async def _editable(self, principal: Principal, agent_id: UUID) -> Agent:
        agent = await self._agents.get(principal, agent_id)
        if not self._agents.can_edit(principal, agent):
            raise ForbiddenError("Bạn không có quyền publish agent này.")
        return agent

    async def _published(self, principal: Principal, agent: Agent) -> AgentVersion:
        if agent.published_version is None:
            raise ValidationError("Agent chưa được publish.")
        return await self._agents.version(principal, agent.id, agent.published_version)

    # ------------------------------------------------------------------ API key
    async def list_api_keys(self, principal: Principal, agent_id: UUID) -> list[ApiKey]:
        await self._editable(principal, agent_id)
        return await self._repo.list_api_keys(principal.tenant_id, agent_id)

    async def create_api_key(
        self, principal: Principal, agent_id: UUID, *, name: str, rate_limit_per_min: int
    ) -> tuple[ApiKey, str]:
        agent = await self._editable(principal, agent_id)
        await self._published(principal, agent)
        if not 1 <= rate_limit_per_min <= 600:
            raise ValidationError("Giới hạn 1–600 yêu cầu/phút.")
        key_id = uuid4()
        token = f"ak_{principal.tenant_id}.{key_id.hex}.{new_secret()}"
        key = await self._repo.create_api_key(
            ApiKey(
                id=key_id,
                tenant_id=principal.tenant_id,
                agent_id=agent.id,
                name=name.strip()[:80] or "API key",
                prefix=token[: len(principal.tenant_id) + 12],
                key_hash=hash_secret(token),
                rate_limit_per_min=rate_limit_per_min,
                created_by=principal.subject,
                created_at=utcnow(),
                last_used_at=None,
                revoked_at=None,
            )
        )
        await self._record(principal, "apikey.created", "api_key", key.id, agent.id)
        return key, token

    async def revoke_api_key(self, principal: Principal, key_id: UUID) -> None:
        key = await self._repo.get_api_key(principal.tenant_id, key_id)
        if key is None:
            raise NotFoundError("Không tìm thấy API key.")
        await self._editable(principal, key.agent_id)
        await self._repo.revoke_api_key(principal.tenant_id, key_id)
        await self._record(principal, "apikey.revoked", "api_key", key.id, key.agent_id)

    async def authenticate_api_key(
        self, token: str, *, end_user: str | None
    ) -> tuple[ApiKey, Principal]:
        """`ak_<tenant>.<id>.<bí mật>` → (key, principal kênh). Mọi lỗi đều là 401 như nhau."""
        if not token.startswith("ak_"):
            raise UnauthorizedError()
        parts = split_token(token[3:])
        if parts is None:
            raise UnauthorizedError()
        tenant, rest = parts
        key_hex = rest.split(".", 1)[0]
        try:
            key_id = UUID(hex=key_hex)
        except ValueError as exc:
            raise UnauthorizedError() from exc
        key = await self._repo.get_api_key(tenant, key_id)
        if (
            key is None
            or not key.active
            or not hmac.compare_digest(key.key_hash, hash_secret(token))
        ):
            raise UnauthorizedError()
        user = end_user or "default"
        if not _USER_RE.match(user):
            raise ValidationError("Định danh người dùng (user) không hợp lệ.")
        if not await self._limiter.hit(f"apikey:{key.id}", key.rate_limit_per_min, 60):
            raise RateLimitedError()
        await self._repo.touch_api_key(tenant, key.id)
        principal = Principal(
            subject=f"apikey:{key.id}:{user}",
            tenant_id=tenant,
            username=None,
            email=None,
            roles=frozenset(),
            channel=f"apikey:{key.id}",
        )
        return key, principal

    # ------------------------------------------------------------------ link chia sẻ
    async def list_shares(self, principal: Principal, agent_id: UUID) -> list[ShareLink]:
        await self._editable(principal, agent_id)
        return await self._repo.list_shares(principal.tenant_id, agent_id)

    @staticmethod
    def _check_origins(origins: list[str]) -> list[str]:
        clean = [o.strip().rstrip("/").lower() for o in origins if o.strip()]
        bad = [o for o in clean if not _ORIGIN_RE.match(o)]
        if bad:
            raise ValidationError(
                f"Origin không hợp lệ: {', '.join(bad[:3])} (dạng https://ten-mien)"
            )
        return clean[:20]

    async def create_share(
        self,
        principal: Principal,
        agent_id: UUID,
        *,
        name: str,
        allowed_origins: list[str],
        rate_limit_per_min: int,
        daily_message_limit: int,
    ) -> tuple[ShareLink, str]:
        agent = await self._editable(principal, agent_id)
        version = await self._published(principal, agent)
        if version.spec.data_classification.value != "public":
            raise ValidationError(
                "Chỉ agent có phân loại dữ liệu 'public' mới tạo được link công khai."
            )
        token = f"{principal.tenant_id}.{new_secret()}"
        now = utcnow()
        link = await self._repo.create_share(
            ShareLink(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                agent_id=agent.id,
                name=name.strip()[:80] or "Link chia sẻ",
                prefix=token[: len(principal.tenant_id) + 7],
                token_hash=hash_secret(token),
                enabled=True,
                allowed_origins=self._check_origins(allowed_origins),
                rate_limit_per_min=max(1, min(rate_limit_per_min, 120)),
                daily_message_limit=max(1, min(daily_message_limit, 100_000)),
                created_by=principal.subject,
                created_at=now,
                updated_at=now,
                last_used_at=None,
            )
        )
        await self._record(principal, "share.created", "share_link", link.id, agent.id)
        return link, token

    async def _managed_share(self, principal: Principal, share_id: UUID) -> ShareLink:
        link = await self._repo.get_share(principal.tenant_id, share_id)
        if link is None:
            raise NotFoundError("Không tìm thấy link chia sẻ.")
        await self._editable(principal, link.agent_id)
        return link

    async def update_share(
        self,
        principal: Principal,
        share_id: UUID,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        allowed_origins: list[str] | None = None,
        rate_limit_per_min: int | None = None,
        daily_message_limit: int | None = None,
    ) -> ShareLink:
        link = await self._managed_share(principal, share_id)
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name.strip()[:80] or link.name
        if enabled is not None:
            changes["enabled"] = enabled
        if allowed_origins is not None:
            changes["allowed_origins"] = self._check_origins(allowed_origins)
        if rate_limit_per_min is not None:
            changes["rate_limit_per_min"] = max(1, min(rate_limit_per_min, 120))
        if daily_message_limit is not None:
            changes["daily_message_limit"] = max(1, min(daily_message_limit, 100_000))
        saved = await self._repo.save_share(replace(link, **changes, updated_at=utcnow()))
        await self._record(principal, "share.updated", "share_link", link.id, link.agent_id)
        return saved

    async def rotate_share(self, principal: Principal, share_id: UUID) -> tuple[ShareLink, str]:
        """Tạo token mới — link cũ ngừng hoạt động ngay."""
        link = await self._managed_share(principal, share_id)
        token = f"{principal.tenant_id}.{new_secret()}"
        saved = await self._repo.save_share(
            replace(
                link,
                token_hash=hash_secret(token),
                prefix=token[: len(principal.tenant_id) + 7],
                updated_at=utcnow(),
            )
        )
        await self._record(principal, "share.rotated", "share_link", link.id, link.agent_id)
        return saved, token

    async def delete_share(self, principal: Principal, share_id: UUID) -> None:
        link = await self._managed_share(principal, share_id)
        await self._repo.delete_share(principal.tenant_id, share_id)
        await self._record(principal, "share.deleted", "share_link", link.id, link.agent_id)

    async def resolve_share(self, token: str) -> ShareLink:
        parts = split_token(token)
        if parts is None:
            raise NotFoundError("Link không tồn tại hoặc đã bị tắt.")
        link = await self._repo.find_share(parts[0], hash_secret(token))
        if link is None or not link.enabled:
            raise NotFoundError("Link không tồn tại hoặc đã bị tắt.")
        return link

    async def share_principal(
        self, link: ShareLink, visitor_id: str | None, embed_origin: str | None
    ) -> Principal:
        """Người dùng ẩn danh của link: kiểm visitor, nơi nhúng, giới hạn tốc độ & số tin/ngày."""
        if not visitor_id or not _VISITOR_RE.match(visitor_id):
            raise ValidationError("Thiếu hoặc sai mã khách (X-Visitor-Id).")
        if (
            embed_origin
            and link.allowed_origins
            and embed_origin.rstrip("/").lower() not in link.allowed_origins
        ):
            raise ForbiddenError("Trang này không được phép nhúng agent.")
        if not await self._limiter.hit(
            f"share:{link.id}:{visitor_id}", link.rate_limit_per_min, 60
        ):
            raise RateLimitedError()
        if not await self._limiter.hit(f"share:{link.id}:day", link.daily_message_limit, 86_400):
            raise RateLimitedError("Link đã hết lượt trong hôm nay.")
        return Principal(
            subject=f"share:{link.id}:{visitor_id}",
            tenant_id=link.tenant_id,
            username=None,
            email=None,
            roles=frozenset(),
            channel=f"share:{link.id}",
        )

    def viewer_principal(self, link: ShareLink, visitor_id: str) -> Principal:
        """Principal chỉ để đọc lại hội thoại của chính visitor (không tính lượt)."""
        if not _VISITOR_RE.match(visitor_id):
            raise ValidationError("Sai mã khách (X-Visitor-Id).")
        return Principal(
            subject=f"share:{link.id}:{visitor_id}",
            tenant_id=link.tenant_id,
            username=None,
            email=None,
            roles=frozenset(),
            channel=f"share:{link.id}",
        )

    async def _record(
        self,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: UUID,
        agent_id: UUID,
    ) -> None:
        await self._audit.record_safely(
            tenant_id=principal.tenant_id,
            actor_id=principal.subject,
            action=action,
            outcome=Outcome.SUCCESS,
            resource_type=resource_type,
            resource_id=str(resource_id),
            details={"agent_id": str(agent_id)},
        )


__all__ = ["ChannelService", "RateLimitedError"]
