"""Read model SQL cho vận hành — chỉ SELECT trên schema conversations (RLS theo tenant)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from aiagent.adapters.db import Database
from aiagent.modules.ops.domain import (
    FeedbackItem,
    OpsFilter,
    ThreadSummary,
    UsageRow,
)

_THREADS = text(
    """
    SELECT * FROM (
      SELECT t.id, t.agent_id, t.user_id, t.title, t.created_at, t.updated_at,
             CASE WHEN t.user_id LIKE 'share:%' THEN 'share' WHEN t.user_id LIKE 'apikey:%' THEN 'api'
                  WHEN t.user_id LIKE 'telegram:%' THEN 'telegram' ELSE 'app' END AS channel,
             (SELECT count(*) FROM conversations.messages m WHERE m.thread_id = t.id)
               AS message_count,
             (SELECT coalesce(sum(r.cost_usd), 0) FROM conversations.runs r
               WHERE r.thread_id = t.id) AS cost_usd,
             (SELECT count(*) FROM conversations.feedback f
               WHERE f.thread_id = t.id AND f.rating = 1) AS thumbs_up,
             (SELECT count(*) FROM conversations.feedback f
               WHERE f.thread_id = t.id AND f.rating = -1) AS thumbs_down
        FROM conversations.threads t
       WHERE NOT t.is_draft
         AND (CAST(:agent_id AS uuid) IS NULL OR t.agent_id = CAST(:agent_id AS uuid))
         AND (CAST(:since AS timestamptz) IS NULL OR t.updated_at >= CAST(:since AS timestamptz))
         AND (CAST(:until AS timestamptz) IS NULL OR t.updated_at < CAST(:until AS timestamptz))
         AND (CAST(:before AS timestamptz) IS NULL OR t.updated_at < CAST(:before AS timestamptz))
    ) x
    WHERE (CAST(:channel AS text) IS NULL OR x.channel = CAST(:channel AS text))
      AND (CAST(:rating AS int) IS NULL
           OR (CAST(:rating AS int) = 1 AND x.thumbs_up > 0)
           OR (CAST(:rating AS int) = -1 AND x.thumbs_down > 0))
    ORDER BY x.updated_at DESC
    LIMIT :limit
    """
)

_FEEDBACK = text(
    """
    SELECT * FROM (
      SELECT f.id, f.thread_id, f.message_id, f.agent_id, f.user_id, f.rating, f.comment,
             f.updated_at AS created_at, m.content AS answer,
             CASE WHEN f.user_id LIKE 'share:%' THEN 'share' WHEN f.user_id LIKE 'apikey:%' THEN 'api'
                  WHEN f.user_id LIKE 'telegram:%' THEN 'telegram' ELSE 'app' END AS channel
        FROM conversations.feedback f
        JOIN conversations.messages m ON m.id = f.message_id
       WHERE (CAST(:agent_id AS uuid) IS NULL OR f.agent_id = CAST(:agent_id AS uuid))
         AND (CAST(:rating AS int) IS NULL OR f.rating = CAST(:rating AS int))
         AND (CAST(:since AS timestamptz) IS NULL OR f.updated_at >= CAST(:since AS timestamptz))
         AND (CAST(:until AS timestamptz) IS NULL OR f.updated_at < CAST(:until AS timestamptz))
    ) x
    WHERE (CAST(:channel AS text) IS NULL OR x.channel = CAST(:channel AS text))
    ORDER BY x.created_at DESC
    LIMIT :limit
    """
)

_USAGE = text(
    """
    SELECT * FROM (
      SELECT (r.started_at AT TIME ZONE 'UTC')::date AS day, r.agent_id,
             CASE WHEN r.user_id LIKE 'share:%' THEN 'share' WHEN r.user_id LIKE 'apikey:%' THEN 'api'
                  WHEN r.user_id LIKE 'telegram:%' THEN 'telegram' ELSE 'app' END AS channel,
             count(*) AS runs,
             count(*) FILTER (WHERE r.status IN ('failed', 'refused')) AS failed_runs,
             coalesce(sum(r.input_tokens + r.cache_read_tokens + r.cache_write_tokens), 0)
               AS input_tokens,
             coalesce(sum(r.output_tokens), 0) AS output_tokens,
             coalesce(sum(r.cost_usd), 0) AS cost_usd
        FROM conversations.runs r
       WHERE r.started_at >= :since AND r.started_at < :until
         AND (CAST(:agent_id AS uuid) IS NULL OR r.agent_id = CAST(:agent_id AS uuid))
       GROUP BY 1, 2, 3
    ) x
    WHERE (CAST(:channel AS text) IS NULL OR x.channel = CAST(:channel AS text))
    ORDER BY x.day, x.agent_id, x.channel
    """
)


def _params(f: OpsFilter) -> dict[str, Any]:
    return {
        "agent_id": f.agent_id,
        "channel": f.channel,
        "since": f.since,
        "until": f.until,
        "rating": f.rating,
    }


class SqlOpsQuery:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def threads(
        self, tenant_id: str, f: OpsFilter, limit: int, before: datetime | None
    ) -> list[ThreadSummary]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (
                await s.execute(_THREADS, {**_params(f), "before": before, "limit": limit})
            ).all()
        return [
            ThreadSummary(
                id=r.id,
                agent_id=r.agent_id,
                user_id=r.user_id,
                channel=r.channel,
                title=r.title,
                created_at=r.created_at,
                updated_at=r.updated_at,
                message_count=int(r.message_count),
                cost_usd=float(r.cost_usd),
                thumbs_up=int(r.thumbs_up),
                thumbs_down=int(r.thumbs_down),
            )
            for r in rows
        ]

    async def feedback(self, tenant_id: str, f: OpsFilter, limit: int) -> list[FeedbackItem]:
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_FEEDBACK, {**_params(f), "limit": limit})).all()
        return [
            FeedbackItem(
                id=r.id,
                thread_id=r.thread_id,
                message_id=int(r.message_id),
                agent_id=r.agent_id,
                user_id=r.user_id,
                channel=r.channel,
                rating=int(r.rating),
                comment=r.comment,
                created_at=r.created_at,
                answer=list(r.answer or []),
            )
            for r in rows
        ]

    async def usage(self, tenant_id: str, f: OpsFilter) -> list[UsageRow]:
        params = {k: v for k, v in _params(f).items() if k != "rating"}
        async with self._db.tenant_session(tenant_id) as s:
            rows = (await s.execute(_USAGE, params)).all()
        return [
            UsageRow(
                day=r.day,
                agent_id=r.agent_id,
                channel=r.channel,
                runs=int(r.runs),
                failed_runs=int(r.failed_runs),
                input_tokens=int(r.input_tokens),
                output_tokens=int(r.output_tokens),
                cost_usd=float(r.cost_usd),
            )
            for r in rows
        ]
