from __future__ import annotations

from typing import Protocol

from aiagent.modules.audit.domain.models import AuditEvent, AuditRecord


class AuditLogPort(Protocol):
    async def append(self, event: AuditEvent) -> AuditRecord: ...

    async def list_recent(
        self, tenant_id: str, *, limit: int = 50, before_seq: int | None = None
    ) -> list[AuditRecord]:
        """Mới nhất trước."""
        ...

    async def list_all_ascending(self, tenant_id: str) -> list[AuditRecord]:
        """Toàn bộ chuỗi theo thứ tự ghi — dùng để kiểm tra toàn vẹn."""
        ...
