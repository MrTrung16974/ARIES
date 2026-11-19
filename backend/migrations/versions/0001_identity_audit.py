"""identity + audit schema, RLS theo tenant, audit append-only

Revision ID: 0001
Revises:
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiagent_app"  # tạo bởi deploy/postgres/init (không owner, NOBYPASSRLS)
TENANT_PREDICATE = "tenant_id = current_setting('app.tenant_id', true)"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")  # dùng cho RAG ở Phase 3

    # ------------------------------------------------------------- identity
    op.execute("CREATE SCHEMA IF NOT EXISTS identity")
    op.execute(f"GRANT USAGE ON SCHEMA identity TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE identity.tenants (
            id          text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
            name        text NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("INSERT INTO identity.tenants (id, name) VALUES ('default', 'Default')")
    op.execute(f"GRANT SELECT ON identity.tenants TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE identity.users (
            tenant_id     text NOT NULL REFERENCES identity.tenants(id),
            subject       text NOT NULL,
            username      text,
            email         text,
            created_at    timestamptz NOT NULL DEFAULT now(),
            last_seen_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, subject)
        )
        """
    )
    _enable_rls("identity.users")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON identity.users TO {APP_ROLE}")

    # ------------------------------------------------------------- audit
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")
    op.execute(f"GRANT USAGE ON SCHEMA audit TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE audit.audit_events (
            seq            bigserial PRIMARY KEY,
            event_id       uuid NOT NULL UNIQUE,
            tenant_id      text NOT NULL REFERENCES identity.tenants(id),
            occurred_at    timestamptz NOT NULL,
            actor_id       text,
            action         text NOT NULL,
            outcome        text NOT NULL CHECK (outcome IN ('success', 'denied', 'failure')),
            resource_type  text,
            resource_id    text,
            ip             text,
            details        jsonb NOT NULL DEFAULT '{}'::jsonb,
            prev_hash      char(64) NOT NULL,
            hash           char(64) NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX audit_events_tenant_seq ON audit.audit_events (tenant_id, seq DESC)")
    _enable_rls("audit.audit_events")
    # Append-only: role ứng dụng chỉ được đọc và thêm, không UPDATE/DELETE/TRUNCATE.
    op.execute(f"GRANT SELECT, INSERT ON audit.audit_events TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SEQUENCE audit.audit_events_seq_seq TO {APP_ROLE}")
    op.execute(
        """
        CREATE FUNCTION audit.forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events là append-only';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_append_only BEFORE UPDATE OR DELETE ON audit.audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit.forbid_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS audit CASCADE")
    op.execute("DROP SCHEMA IF EXISTS identity CASCADE")
