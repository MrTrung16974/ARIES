"""Lưu cấu hình nhà cung cấp LLM vào PostgreSQL (llm.providers, RLS theo tenant)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from aiagent.adapters.db import Database
from aiagent.modules.runtime.domain import ProviderRecord

_LIST = text(
    """
    SELECT tenant_id, provider_id, enabled, base_url, models, extra, has_api_key,
           updated_by, updated_at
      FROM llm.providers
     ORDER BY provider_id
    """
)
_GET = text(
    """
    SELECT tenant_id, provider_id, enabled, base_url, models, extra, has_api_key,
           updated_by, updated_at
      FROM llm.providers
     WHERE provider_id = :provider_id
    """
)
_UPSERT = text(
    """
    INSERT INTO llm.providers
      (tenant_id, provider_id, enabled, base_url, models, extra, has_api_key, updated_by, updated_at)
    VALUES
      (:tenant_id, :provider_id, :enabled, :base_url, CAST(:models AS jsonb),
       CAST(:extra AS jsonb), :has_api_key, :updated_by, now())
    ON CONFLICT (tenant_id, provider_id) DO UPDATE
       SET enabled = EXCLUDED.enabled,
           base_url = EXCLUDED.base_url,
           models = EXCLUDED.models,
           extra = EXCLUDED.extra,
           has_api_key = EXCLUDED.has_api_key,
           updated_by = EXCLUDED.updated_by,
           updated_at = now()
    RETURNING tenant_id, provider_id, enabled, base_url, models, extra, has_api_key,
              updated_by, updated_at
    """
)
_DELETE = text("DELETE FROM llm.providers WHERE provider_id = :provider_id")


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _row(m: Any) -> ProviderRecord:
    return ProviderRecord(
        tenant_id=str(m["tenant_id"]),
        provider_id=str(m["provider_id"]),
        enabled=bool(m["enabled"]),
        base_url=m["base_url"],
        models=[str(x) for x in (_load(m["models"]) or [])],
        extra=dict(_load(m["extra"]) or {}),
        has_api_key=bool(m["has_api_key"]),
        updated_by=str(m["updated_by"]),
        updated_at=m["updated_at"],
    )


class SqlProviderRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_for_tenant(self, tenant_id: str) -> list[ProviderRecord]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_LIST)).mappings().all()
            return [_row(r) for r in rows]

    async def get(self, tenant_id: str, provider_id: str) -> ProviderRecord | None:
        async with self._db.tenant_session(tenant_id) as s:
            row = (await s.execute(_GET, {"provider_id": provider_id})).mappings().first()
            return _row(row) if row is not None else None

    async def upsert(
        self,
        tenant_id: str,
        provider_id: str,
        *,
        enabled: bool,
        base_url: str | None,
        models: list[str],
        extra: dict[str, Any],
        has_api_key: bool,
        updated_by: str,
    ) -> ProviderRecord:
        async with self._db.tenant_session(tenant_id) as s:
            row = (
                (
                    await s.execute(
                        _UPSERT,
                        {
                            "tenant_id": tenant_id,
                            "provider_id": provider_id,
                            "enabled": enabled,
                            "base_url": base_url,
                            "models": json.dumps(models),
                            "extra": json.dumps(extra),
                            "has_api_key": has_api_key,
                            "updated_by": updated_by,
                        },
                    )
                )
                .mappings()
                .one()
            )
            return _row(row)

    async def delete(self, tenant_id: str, provider_id: str) -> bool:
        async with self._db.tenant_session(tenant_id) as s:
            res = await s.execute(_DELETE, {"provider_id": provider_id})
            return bool(getattr(res, "rowcount", 0))  # CursorResult (DELETE) có rowcount
