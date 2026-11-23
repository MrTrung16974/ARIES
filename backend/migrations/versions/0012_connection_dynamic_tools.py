"""Tool động theo từng connection (OpenAPI, gRPC reflection…) + ghi đè mức rủi ro (ADR-0023)

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE connections.connections
          ADD COLUMN tools           jsonb NOT NULL DEFAULT '[]'::jsonb,
          ADD COLUMN risk_overrides  jsonb NOT NULL DEFAULT '{}'::jsonb,
          ADD COLUMN tools_synced_at timestamptz
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE connections.connections
          DROP COLUMN IF EXISTS tools_synced_at,
          DROP COLUMN IF EXISTS risk_overrides,
          DROP COLUMN IF EXISTS tools
        """
    )
