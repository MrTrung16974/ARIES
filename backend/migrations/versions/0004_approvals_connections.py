"""HITL approvals + connections (ADR-0016)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
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
    # ------------------------------------------------------------- runs: trạng thái chờ duyệt
    op.execute("ALTER TABLE conversations.runs DROP CONSTRAINT runs_status_check")
    op.execute(
        "ALTER TABLE conversations.runs ADD CONSTRAINT runs_status_check CHECK (status IN "
        "('running','completed','failed','refused','cancelled','awaiting_approval'))"
    )

    # ------------------------------------------------------------- approvals
    op.execute(
        """
        CREATE TABLE conversations.approvals (
            id              uuid PRIMARY KEY,
            tenant_id       text NOT NULL REFERENCES identity.tenants(id),
            thread_id       uuid NOT NULL REFERENCES conversations.threads(id),
            run_id          uuid NOT NULL REFERENCES conversations.runs(id),
            agent_id        uuid NOT NULL REFERENCES agents.agents(id),
            requester_id    text NOT NULL,
            tool_use_id     text NOT NULL,
            tool            text NOT NULL,
            risk            text NOT NULL,
            reason          text NOT NULL DEFAULT '',
            arguments       jsonb NOT NULL DEFAULT '{}'::jsonb,
            approver_roles  jsonb NOT NULL DEFAULT '[]'::jsonb,
            connection_id   text,
            status          text NOT NULL
                            CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
            decided_by      text,
            decision_note   text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            decided_at      timestamptz,
            CHECK ((status = 'pending') = (decided_at IS NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX approvals_status ON conversations.approvals (tenant_id, status, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX approvals_thread_tool ON conversations.approvals (thread_id, tool_use_id)"
    )
    _enable_rls("conversations.approvals")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON conversations.approvals TO {APP_ROLE}")

    # ------------------------------------------------------------- connections
    # Chỉ lưu cấu hình không bí mật; secret nằm ở OpenBao (secret_path).
    op.execute("CREATE SCHEMA connections")
    op.execute(f"GRANT USAGE ON SCHEMA connections TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE connections.connections (
            id              uuid PRIMARY KEY,
            tenant_id       text NOT NULL REFERENCES identity.tenants(id),
            name            text NOT NULL,
            connector_id    text NOT NULL,
            scope           text NOT NULL CHECK (scope IN ('personal', 'workspace')),
            owner_id        text NOT NULL,
            config          jsonb NOT NULL DEFAULT '{}'::jsonb,
            secret_keys     jsonb NOT NULL DEFAULT '[]'::jsonb,
            status          text NOT NULL CHECK (status IN ('active', 'disabled', 'deleted')),
            last_test_ok    boolean,
            last_test_note  text,
            last_test_at    timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Tên duy nhất trong tenant (bỏ qua bản đã xóa mềm).
    op.execute(
        "CREATE UNIQUE INDEX connections_name ON connections.connections (tenant_id, name) "
        "WHERE status <> 'deleted'"
    )
    op.execute("CREATE INDEX connections_owner ON connections.connections (tenant_id, owner_id)")
    _enable_rls("connections.connections")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON connections.connections TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS connections CASCADE")
    op.execute("DROP TABLE IF EXISTS conversations.approvals")
    op.execute("UPDATE conversations.runs SET status = 'failed' WHERE status = 'awaiting_approval'")
    op.execute("ALTER TABLE conversations.runs DROP CONSTRAINT runs_status_check")
    op.execute(
        "ALTER TABLE conversations.runs ADD CONSTRAINT runs_status_check CHECK (status IN "
        "('running','completed','failed','refused','cancelled'))"
    )
