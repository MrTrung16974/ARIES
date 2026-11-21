"""Read model vận hành chạy trên InMemoryConversationRepository (cùng ngữ nghĩa SqlOpsQuery)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aiagent.modules.ops.domain import (
    FeedbackItem,
    OpsFilter,
    ThreadSummary,
    UsageRow,
    channel_of,
)
from aiagent.shared_kernel.usage import Usage
from tests.fakes import InMemoryConversationRepository


def _in_range(ts: datetime, f: OpsFilter) -> bool:
    return (f.since is None or ts >= f.since) and (f.until is None or ts < f.until)


class InMemoryOpsQuery:
    def __init__(self, repo: InMemoryConversationRepository) -> None:
        self._repo = repo

    def _feedback(self) -> list[Any]:
        return list(self._repo.__dict__.setdefault("feedback", {}).values())

    async def threads(
        self, tenant_id: str, f: OpsFilter, limit: int, before: datetime | None
    ) -> list[ThreadSummary]:
        out: list[ThreadSummary] = []
        for t in sorted(self._repo.threads.values(), key=lambda t: t.updated_at, reverse=True):
            fb = [x for x in self._feedback() if x.thread_id == t.id]
            up = sum(1 for x in fb if x.rating == 1)
            down = sum(1 for x in fb if x.rating == -1)
            ok = (
                t.tenant_id == tenant_id
                and not t.is_draft
                and (f.agent_id is None or t.agent_id == f.agent_id)
                and (f.channel is None or channel_of(t.user_id) == f.channel)
                and _in_range(t.updated_at, f)
                and (before is None or t.updated_at < before)
                and (f.rating is None or (up if f.rating == 1 else down) > 0)
            )
            if not ok:
                continue
            cost = sum(
                r.get("cost", 0.0) for r in self._repo.runs.values() if r.get("thread_id") == t.id
            )
            out.append(
                ThreadSummary(
                    id=t.id,
                    agent_id=t.agent_id,
                    user_id=t.user_id,
                    channel=channel_of(t.user_id),
                    title=t.title,
                    created_at=t.created_at,
                    updated_at=t.updated_at,
                    message_count=sum(1 for m in self._repo.messages if m.thread_id == t.id),
                    cost_usd=cost,
                    thumbs_up=up,
                    thumbs_down=down,
                )
            )
        return out[:limit]

    async def feedback(self, tenant_id: str, f: OpsFilter, limit: int) -> list[FeedbackItem]:
        messages = {m.id: m for m in self._repo.messages}
        items = [
            FeedbackItem(
                id=x.id,
                thread_id=x.thread_id,
                message_id=x.message_id,
                agent_id=x.agent_id,
                user_id=x.user_id,
                channel=channel_of(x.user_id),
                rating=x.rating,
                comment=x.comment,
                created_at=x.created_at,
                answer=messages[x.message_id].content,
            )
            for x in self._feedback()
            if x.tenant_id == tenant_id
            and (f.agent_id is None or x.agent_id == f.agent_id)
            and (f.rating is None or x.rating == f.rating)
            and (f.channel is None or channel_of(x.user_id) == f.channel)
            and _in_range(x.created_at, f)
        ]
        return sorted(items, key=lambda i: i.created_at, reverse=True)[:limit]

    async def usage(self, tenant_id: str, f: OpsFilter) -> list[UsageRow]:
        groups: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in self._repo.runs.values():
            if "agent_id" not in r or not _in_range(r["started_at"], f):
                continue
            channel = channel_of(r["user_id"])
            if (f.agent_id and r["agent_id"] != f.agent_id) or (f.channel and channel != f.channel):
                continue
            g = groups.setdefault(
                (r["started_at"].date(), r["agent_id"], channel),
                {"runs": 0, "failed": 0, "in": 0, "out": 0, "cost": 0.0},
            )
            usage: Usage = r.get("usage") or Usage()
            g["runs"] += 1
            g["failed"] += int(str(r["status"]) in ("failed", "refused"))
            g["in"] += (
                usage.input_tokens
                + usage.cache_read_input_tokens
                + usage.cache_creation_input_tokens
            )
            g["out"] += usage.output_tokens
            g["cost"] += r.get("cost", 0.0)
        return [
            UsageRow(day, agent, ch, g["runs"], g["failed"], g["in"], g["out"], g["cost"])
            for (day, agent, ch), g in sorted(groups.items(), key=lambda kv: str(kv[0]))
        ]
