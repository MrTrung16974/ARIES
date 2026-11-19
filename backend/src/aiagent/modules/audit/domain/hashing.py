"""Hash-chain: mỗi bản ghi chứa hash của bản ghi trước trong cùng tenant.

Sửa hoặc xóa một bản ghi ở giữa làm đứt chuỗi và bị `find_broken_link` phát hiện.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from aiagent.modules.audit.domain.models import AuditEvent, AuditRecord

GENESIS_HASH = "0" * 64


def _canonical(event: AuditEvent) -> str:
    payload = {
        "event_id": str(event.event_id),
        "tenant_id": event.tenant_id,
        "occurred_at": event.occurred_at.isoformat(),
        "actor_id": event.actor_id,
        "action": event.action,
        "outcome": event.outcome.value,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "ip": event.ip,
        "details": event.details,
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def compute_hash(prev_hash: str, event: AuditEvent) -> str:
    return hashlib.sha256(f"{prev_hash}|{_canonical(event)}".encode()).hexdigest()


def find_broken_link(
    records: Sequence[AuditRecord], expected_prev: str = GENESIS_HASH
) -> int | None:
    """Trả về `seq` của bản ghi đầu tiên sai chuỗi, hoặc None nếu chuỗi nguyên vẹn."""
    prev = expected_prev
    for record in records:
        if record.prev_hash != prev or record.hash != compute_hash(prev, record.event):
            return record.seq
        prev = record.hash
    return None
