from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Thời điểm hiện tại, luôn có timezone UTC."""
    return datetime.now(UTC)
