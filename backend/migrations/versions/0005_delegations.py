"""Master agent: bản ghi giao việc (delegation) giữa run cha và run con (ADR-0017)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiagent_app"
TENANT_PREDICATE = "tenant_id = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conversations.delegations (
            id               uuid PRIMARY KEY,
            tenant_id        text NOT NULL REFERENCES identity.tenants(id),
            parent_thread_id uuid NOT NULL REFERENCES conversations.threads(id),
            parent_run_id    uuid NOT NULL REFERENCES conversations.runs(id),
            tool_use_id      text NOT NULL,
            agent_id         uuid NOT NULL REFERENCES agents.agents(id),
            task             text NOT NULL,
            child_thread_id  uuid REFERENCES conversations.threads(id),
            child_run_id     uuid REFERENCES conversations.runs(id),
            status           text NOT NULL CHECK (status IN
                               ('running', 'completed', 'failed', 'awaiting_approval')),
            result           text,
            tainted          boolean NOT NULL DEFAULT false,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX delegations_parent ON conversations.delegations "
        "(parent_thread_id, tool_use_id)"
    )
    op.execute("ALTER TABLE conversations.delegations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE conversations.delegations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON conversations.delegations "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON conversations.delegations TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conversations.delegations")
