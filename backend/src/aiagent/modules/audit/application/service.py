from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from aiagent.modules.audit.domain import (
    AuditEvent,
    AuditLogPort,
    AuditRecord,
    Outcome,
    find_broken_link,
)

logger = logging.getLogger(__name__)

_SENSITIVE_KEY = re.compile(
    r"pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key",
    re.IGNORECASE,
)
REDACTED = "***"


def redact(value: Any) -> Any:
    """Che giá trị của các khóa nhạy cảm (đệ quy) trước khi ghi audit."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


@dataclass(frozen=True)
class ChainStatus:
    intact: bool
    records_checked: int
    broken_at_seq: int | None


class AuditService:
    def __init__(self, log: AuditLogPort) -> None:
        self._log = log

    async def record(
        self,
        *,
        tenant_id: str,
        actor_id: str | None,
        action: str,
        outcome: Outcome,
        resource_type: str | None = None,
        resource_id: str | None = None,
        ip: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditRecord:
        event = AuditEvent(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            outcome=outcome,
            resource_type=resource_type,
            resource_id=resource_id,
            ip=ip,
            details=redact(details or {}),
        )
        return await self._log.append(event)

    async def record_safely(self, **kwargs: Any) -> None:
        """Như `record` nhưng không làm hỏng request gốc nếu ghi audit lỗi (lỗi vẫn được log)."""
        try:
            await self.record(**kwargs)
        except Exception:
            logger.exception("Ghi audit thất bại", extra={"action": kwargs.get("action")})

    async def recent(
        self, tenant_id: str, *, limit: int = 50, before_seq: int | None = None
    ) -> list[AuditRecord]:
        return await self._log.list_recent(tenant_id, limit=limit, before_seq=before_seq)

    async def verify_chain(self, tenant_id: str) -> ChainStatus:
        records = await self._log.list_all_ascending(tenant_id)
        broken = find_broken_link(records)
        return ChainStatus(
            intact=broken is None, records_checked=len(records), broken_at_seq=broken
        )
