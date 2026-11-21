"""Kênh publish (API key, link chia sẻ) + feedback câu trả lời — ADR-0020

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiagent_app"
TENANT_PREDICATE = "tenant_id = current_setting('app.tenant_id', true)"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )


def upgrade() -> None:
    op.execute("CREATE SCHEMA channels")
    op.execute(f"GRANT USAGE ON SCHEMA channels TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE channels.api_keys (
            id                  uuid PRIMARY KEY,
            tenant_id           text NOT NULL REFERENCES identity.tenants(id),
            agent_id            uuid NOT NULL REFERENCES agents.agents(id),
            name                text NOT NULL,
            prefix              text NOT NULL,
            key_hash            text NOT NULL UNIQUE,
            rate_limit_per_min  integer NOT NULL CHECK (rate_limit_per_min BETWEEN 1 AND 600),
            created_by          text NOT NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            last_used_at        timestamptz,
            revoked_at          timestamptz
        )
        """
    )
    op.execute("CREATE INDEX api_keys_agent ON channels.api_keys (tenant_id, agent_id)")
    _enable_rls("channels.api_keys")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON channels.api_keys TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE channels.share_links (
            id                   uuid PRIMARY KEY,
            tenant_id            text NOT NULL REFERENCES identity.tenants(id),
            agent_id             uuid NOT NULL REFERENCES agents.agents(id),
            name                 text NOT NULL,
            prefix               text NOT NULL,
            token_hash           text NOT NULL UNIQUE,
            enabled              boolean NOT NULL DEFAULT true,
            allowed_origins      jsonb NOT NULL DEFAULT '[]'::jsonb,
            rate_limit_per_min   integer NOT NULL CHECK (rate_limit_per_min BETWEEN 1 AND 120),
            daily_message_limit  integer NOT NULL CHECK (daily_message_limit BETWEEN 1 AND 100000),
            created_by           text NOT NULL,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            last_used_at         timestamptz
        )
        """
    )
    op.execute("CREATE INDEX share_links_agent ON channels.share_links (tenant_id, agent_id)")
    _enable_rls("channels.share_links")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON channels.share_links TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE conversations.feedback (
            id          uuid PRIMARY KEY,
            tenant_id   text NOT NULL REFERENCES identity.tenants(id),
            thread_id   uuid NOT NULL REFERENCES conversations.threads(id),
            message_id  bigint NOT NULL REFERENCES conversations.messages(id),
            agent_id    uuid NOT NULL REFERENCES agents.agents(id),
            user_id     text NOT NULL,
            rating      smallint NOT NULL CHECK (rating IN (-1, 1)),
            comment     text NOT NULL DEFAULT '',
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (message_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX feedback_agent ON conversations.feedback (tenant_id, agent_id, created_at)"
    )
    _enable_rls("conversations.feedback")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON conversations.feedback TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversations.feedback")
    op.execute("DROP SCHEMA IF EXISTS channels CASCADE")
