"""Kênh Telegram (ADR-0020 §5): bot gắn với một agent; token nằm ở OpenBao

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiagent_app"
TENANT_PREDICATE = "tenant_id = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE channels.telegram_bots (
            id                  uuid PRIMARY KEY,
            tenant_id           text NOT NULL REFERENCES identity.tenants(id),
            agent_id            uuid NOT NULL REFERENCES agents.agents(id),
            name                text NOT NULL,
            bot_user_id         bigint NOT NULL,
            bot_username        text NOT NULL,
            mode                text NOT NULL CHECK (mode IN ('webhook', 'polling')),
            webhook_secret_hash text NOT NULL,
            enabled             boolean NOT NULL DEFAULT true,
            allowed_chat_ids    jsonb NOT NULL DEFAULT '[]'::jsonb,
            rate_limit_per_min  integer NOT NULL DEFAULT 20
                                CHECK (rate_limit_per_min BETWEEN 1 AND 120),
            update_offset       bigint NOT NULL DEFAULT 0,
            created_by          text NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            last_used_at        timestamptz,
            UNIQUE (tenant_id, bot_user_id)
        )
        """
    )
    op.execute("CREATE INDEX telegram_bots_agent ON channels.telegram_bots (tenant_id, agent_id)")
    table = "channels.telegram_bots"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS channels.telegram_bots")
