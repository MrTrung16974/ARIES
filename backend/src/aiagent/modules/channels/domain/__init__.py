"""Kênh publish agent ra ngoài (ADR-0020): API key, link chia sẻ / widget nhúng, Telegram."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def split_token(token: str) -> tuple[str, str] | None:
    """`<tenant>.<phần còn lại>` → (tenant, phần còn lại). None nếu sai định dạng."""
    tenant, sep, rest = token.partition(".")
    if not sep or not tenant or not rest or len(token) > 300:
        return None
    return tenant, rest


@dataclass(frozen=True)
class ApiKey:
    id: UUID
    tenant_id: str
    agent_id: UUID
    name: str
    prefix: str  # vài ký tự đầu để nhận diện trên UI
    key_hash: str = field(repr=False)
    rate_limit_per_min: int
    created_by: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True)
class ShareLink:
    id: UUID
    tenant_id: str
    agent_id: UUID
    name: str
    prefix: str
    token_hash: str = field(repr=False)
    enabled: bool
    allowed_origins: list[str]  # rỗng = nhúng ở đâu cũng được
    rate_limit_per_min: int
    daily_message_limit: int
    created_by: str
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None


class ChannelRepositoryPort(Protocol):
    async def create_api_key(self, key: ApiKey) -> ApiKey: ...

    async def list_api_keys(self, tenant_id: str, agent_id: UUID) -> list[ApiKey]: ...

    async def get_api_key(self, tenant_id: str, key_id: UUID) -> ApiKey | None: ...

    async def revoke_api_key(self, tenant_id: str, key_id: UUID) -> None: ...

    async def touch_api_key(self, tenant_id: str, key_id: UUID) -> None: ...

    async def create_share(self, link: ShareLink) -> ShareLink: ...

    async def list_shares(self, tenant_id: str, agent_id: UUID) -> list[ShareLink]: ...

    async def get_share(self, tenant_id: str, share_id: UUID) -> ShareLink | None: ...

    async def find_share(self, tenant_id: str, token_hash: str) -> ShareLink | None: ...

    async def save_share(self, link: ShareLink) -> ShareLink: ...

    async def delete_share(self, tenant_id: str, share_id: UUID) -> None: ...


class RateLimiterPort(Protocol):
    async def hit(self, key: str, limit: int, window_s: int) -> bool:
        """Ghi một lượt; False nếu đã vượt `limit` trong cửa sổ hiện tại."""
        ...


__all__ = [
    "ApiKey",
    "ChannelRepositoryPort",
    "RateLimiterPort",
    "ShareLink",
    "hash_secret",
    "new_secret",
    "split_token",
]
