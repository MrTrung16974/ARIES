"""Tự động hóa: lịch chạy (cron | gmail) + lịch sử chạy — ADR-0021

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
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
    op.execute("CREATE SCHEMA automation")
    op.execute(f"GRANT USAGE ON SCHEMA automation TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE automation.schedules (
            id              uuid PRIMARY KEY,
            tenant_id       text NOT NULL REFERENCES identity.tenants(id),
            owner_id        text NOT NULL,
            agent_id        uuid NOT NULL REFERENCES agents.agents(id),
            name            text NOT NULL,
            kind            text NOT NULL CHECK (kind IN ('cron', 'gmail')),
            cron            text NOT NULL,
            timezone        text NOT NULL,
            prompt          text NOT NULL,
            connection_id   uuid REFERENCES connections.connections(id),
            trigger_config  jsonb NOT NULL DEFAULT '{}'::jsonb,
            state           jsonb NOT NULL DEFAULT '{}'::jsonb,
            enabled         boolean NOT NULL DEFAULT true,
            next_run_at     timestamptz,
            last_run_at     timestamptz,
            last_status     text,
            failure_count   integer NOT NULL DEFAULT 0,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX schedules_due ON automation.schedules (tenant_id, next_run_at) WHERE enabled"
    )
    op.execute("CREATE INDEX schedules_owner ON automation.schedules (tenant_id, owner_id)")
    _enable_rls("automation.schedules")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON automation.schedules TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE automation.schedule_runs (
            id           uuid PRIMARY KEY,
            tenant_id    text NOT NULL REFERENCES identity.tenants(id),
            schedule_id  uuid NOT NULL REFERENCES automation.schedules(id) ON DELETE CASCADE,
            thread_id    uuid REFERENCES conversations.threads(id),
            status       text NOT NULL,
            trigger_ref  text NOT NULL,
            title        text NOT NULL DEFAULT '',
            error        text,
            started_at   timestamptz NOT NULL DEFAULT now(),
            finished_at  timestamptz,
            UNIQUE (schedule_id, trigger_ref)
        )
        """
    )
    op.execute(
        "CREATE INDEX schedule_runs_recent ON automation.schedule_runs (schedule_id, started_at DESC)"
    )
    _enable_rls("automation.schedule_runs")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON automation.schedule_runs TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS automation CASCADE")
