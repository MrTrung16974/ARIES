from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy import text

from aiagent.adapters.db import Database
from aiagent.modules.channels.domain import ApiKey, ShareLink
from aiagent.shared_kernel.errors import NotFoundError

_KEY_COLS = """id, tenant_id, agent_id, name, prefix, key_hash, rate_limit_per_min, created_by,
               created_at, last_used_at, revoked_at"""
_SHARE_COLS = """id, tenant_id, agent_id, name, prefix, token_hash, enabled, allowed_origins,
                 rate_limit_per_min, daily_message_limit, created_by, created_at, updated_at,
                 last_used_at"""

_INSERT_KEY = text(
    """
    INSERT INTO channels.api_keys (id, tenant_id, agent_id, name, prefix, key_hash,
                                   rate_limit_per_min, created_by)
    VALUES (:id, :tenant_id, :agent_id, :name, :prefix, :key_hash, :rate, :created_by)
    """
)
_LIST_KEYS = text(
    "SELECT id, tenant_id, agent_id, name, prefix, key_hash, rate_limit_per_min, created_by, "
    "created_at, last_used_at, revoked_at FROM channels.api_keys "
    "WHERE agent_id = :agent_id ORDER BY created_at DESC"
)
_GET_KEY = text(
    "SELECT id, tenant_id, agent_id, name, prefix, key_hash, rate_limit_per_min, created_by, "
    "created_at, last_used_at, revoked_at FROM channels.api_keys WHERE id = :id"
)
_REVOKE_KEY = text(
    "UPDATE channels.api_keys SET revoked_at = now() WHERE id = :id AND revoked_at IS NULL"
)
_TOUCH_KEY = text("UPDATE channels.api_keys SET last_used_at = now() WHERE id = :id")

_INSERT_SHARE = text(
    """
    INSERT INTO channels.share_links (id, tenant_id, agent_id, name, prefix, token_hash, enabled,
                                      allowed_origins, rate_limit_per_min, daily_message_limit,
                                      created_by)
    VALUES (:id, :tenant_id, :agent_id, :name, :prefix, :token_hash, :enabled,
            CAST(:allowed_origins AS jsonb), :rate, :daily, :created_by)
    """
)
_SHARE_SELECT = (
    "SELECT id, tenant_id, agent_id, name, prefix, token_hash, enabled, allowed_origins, "
    "rate_limit_per_min, daily_message_limit, created_by, created_at, updated_at, last_used_at "
    "FROM channels.share_links "
)
_LIST_SHARES = text(_SHARE_SELECT + "WHERE agent_id = :agent_id ORDER BY created_at DESC")
_GET_SHARE = text(_SHARE_SELECT + "WHERE id = :id")
_FIND_SHARE = text(_SHARE_SELECT + "WHERE token_hash = :token_hash")
_SAVE_SHARE = text(
    """
    UPDATE channels.share_links
       SET name = :name, prefix = :prefix, token_hash = :token_hash, enabled = :enabled,
           allowed_origins = CAST(:allowed_origins AS jsonb), rate_limit_per_min = :rate,
           daily_message_limit = :daily, updated_at = now()
     WHERE id = :id
    """
)
_TOUCH_SHARE = text("UPDATE channels.share_links SET last_used_at = now() WHERE id = :id")
_DELETE_SHARE = text("DELETE FROM channels.share_links WHERE id = :id")


def _key(m: Any) -> ApiKey:
    return ApiKey(
        id=m["id"],
        tenant_id=m["tenant_id"],
        agent_id=m["agent_id"],
        name=m["name"],
        prefix=m["prefix"],
        key_hash=m["key_hash"],
        rate_limit_per_min=int(m["rate_limit_per_min"]),
        created_by=m["created_by"],
        created_at=m["created_at"],
        last_used_at=m["last_used_at"],
        revoked_at=m["revoked_at"],
    )


def _share(m: Any) -> ShareLink:
    origins = m["allowed_origins"]
    return ShareLink(
        id=m["id"],
        tenant_id=m["tenant_id"],
        agent_id=m["agent_id"],
        name=m["name"],
        prefix=m["prefix"],
        token_hash=m["token_hash"],
        enabled=bool(m["enabled"]),
        allowed_origins=list(json.loads(origins) if isinstance(origins, str) else origins or []),
        rate_limit_per_min=int(m["rate_limit_per_min"]),
        daily_message_limit=int(m["daily_message_limit"]),
        created_by=m["created_by"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
        last_used_at=m["last_used_at"],
    )


class SqlChannelRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------ API key
    async def create_api_key(self, key: ApiKey) -> ApiKey:
        async with self._db.tenant_session(key.tenant_id) as s:
            await s.execute(
                _INSERT_KEY,
                {
                    "id": key.id,
                    "tenant_id": key.tenant_id,
                    "agent_id": key.agent_id,
                    "name": key.name,
                    "prefix": key.prefix,
                    "key_hash": key.key_hash,
                    "rate": key.rate_limit_per_min,
                    "created_by": key.created_by,
                },
            )
        found = await self.get_api_key(key.tenant_id, key.id)
        if found is None:
            raise NotFoundError()
        return found

    async def list_api_keys(self, tenant_id: str, agent_id: UUID) -> list[ApiKey]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST_KEYS, {"agent_id": agent_id})).all()
        return [_key(r._mapping) for r in rows]

    async def get_api_key(self, tenant_id: str, key_id: UUID) -> ApiKey | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET_KEY, {"id": key_id})).first()
        return _key(row._mapping) if row else None

    async def revoke_api_key(self, tenant_id: str, key_id: UUID) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(_REVOKE_KEY, {"id": key_id})

    async def touch_api_key(self, tenant_id: str, key_id: UUID) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(_TOUCH_KEY, {"id": key_id})

    # ------------------------------------------------------------------ link chia sẻ
    def _share_params(self, link: ShareLink) -> dict[str, Any]:
        return {
            "id": link.id,
            "tenant_id": link.tenant_id,
            "agent_id": link.agent_id,
            "name": link.name,
            "prefix": link.prefix,
            "token_hash": link.token_hash,
            "enabled": link.enabled,
            "allowed_origins": json.dumps(link.allowed_origins),
            "rate": link.rate_limit_per_min,
            "daily": link.daily_message_limit,
            "created_by": link.created_by,
        }

    async def create_share(self, link: ShareLink) -> ShareLink:
        async with self._db.tenant_session(link.tenant_id) as s:
            await s.execute(_INSERT_SHARE, self._share_params(link))
        found = await self.get_share(link.tenant_id, link.id)
        if found is None:
            raise NotFoundError()
        return found

    async def list_shares(self, tenant_id: str, agent_id: UUID) -> list[ShareLink]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST_SHARES, {"agent_id": agent_id})).all()
        return [_share(r._mapping) for r in rows]

    async def get_share(self, tenant_id: str, share_id: UUID) -> ShareLink | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET_SHARE, {"id": share_id})).first()
        return _share(row._mapping) if row else None

    async def find_share(self, tenant_id: str, token_hash: str) -> ShareLink | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_FIND_SHARE, {"token_hash": token_hash})).first()
            if row is not None:
                await s.execute(_TOUCH_SHARE, {"id": row._mapping["id"]})
        return _share(row._mapping) if row else None

    async def save_share(self, link: ShareLink) -> ShareLink:
        async with self._db.tenant_session(link.tenant_id) as s:
            await s.execute(_SAVE_SHARE, self._share_params(link))
        found = await self.get_share(link.tenant_id, link.id)
        if found is None:
            raise NotFoundError()
        return found

    async def delete_share(self, tenant_id: str, share_id: UUID) -> None:
        async with self._db.tenant_session(tenant_id) as s:
            await s.execute(_DELETE_SHARE, {"id": share_id})


class RedisRateLimiter:
    """Cửa sổ cố định: INCR + EXPIRE (nguyên tử bằng pipeline)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, limit: int, window_s: int) -> bool:
        bucket = int(time.time() // window_s)
        k = f"aries:rl:{key}:{bucket}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(k)
            pipe.expire(k, window_s + 5)
            count, _ = await pipe.execute()
        return int(count) <= limit
