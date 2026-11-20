"""builder: phiên Agent Architect + lịch sử (append-only)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
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
    op.execute("CREATE SCHEMA builder")
    op.execute(f"GRANT USAGE ON SCHEMA builder TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE builder.sessions (
            id              uuid PRIMARY KEY,
            tenant_id       text NOT NULL REFERENCES identity.tenants(id),
            user_id         text NOT NULL,
            agent_id        uuid REFERENCES agents.agents(id),
            base_version    integer,
            status          text NOT NULL
                            CHECK (status IN ('drafting', 'awaiting_user', 'ready', 'published')),
            brief           jsonb,
            draft_spec      jsonb,
            questions       jsonb NOT NULL DEFAULT '[]'::jsonb,
            clarify_rounds  integer NOT NULL DEFAULT 0,
            eval_report     jsonb,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX builder_sessions_user ON builder.sessions (tenant_id, user_id, updated_at DESC)"
    )
    _enable_rls("builder.sessions")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON builder.sessions TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE builder.messages (
            id          bigserial PRIMARY KEY,
            tenant_id   text NOT NULL REFERENCES identity.tenants(id),
            session_id  uuid NOT NULL REFERENCES builder.sessions(id),
            role        text NOT NULL CHECK (role IN ('user', 'architect')),
            content     text NOT NULL,
            events      jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX builder_messages_session ON builder.messages (session_id, id)")
    _enable_rls("builder.messages")
    op.execute(f"GRANT SELECT, INSERT ON builder.messages TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SEQUENCE builder.messages_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS builder CASCADE")
