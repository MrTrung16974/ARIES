"""Lịch chạy: kiểu kích hoạt theo connector có trigger (gmail, kafka…) thay vì danh sách cố định

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE automation.schedules DROP CONSTRAINT IF EXISTS schedules_kind_check")
    op.execute(
        "ALTER TABLE automation.schedules ADD CONSTRAINT schedules_kind_check "
        "CHECK (kind ~ '^[a-z][a-z0-9_]{1,62}$')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE automation.schedules DROP CONSTRAINT IF EXISTS schedules_kind_check")
    op.execute(
        "ALTER TABLE automation.schedules ADD CONSTRAINT schedules_kind_check "
        "CHECK (kind IN ('cron', 'gmail'))"
    )
