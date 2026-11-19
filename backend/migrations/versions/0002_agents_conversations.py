"""agents (spec có version, immutable) + conversations (thread, message, run)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
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
    # ------------------------------------------------------------- agents
    op.execute("CREATE SCHEMA agents")
    op.execute(f"GRANT USAGE ON SCHEMA agents TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE agents.agents (
            id                 uuid PRIMARY KEY,
            tenant_id          text NOT NULL REFERENCES identity.tenants(id),
            name               text NOT NULL,
            description        text NOT NULL DEFAULT '',
            owner_id           text NOT NULL,
            status             text NOT NULL CHECK (status IN ('draft', 'published', 'archived')),
            latest_version     integer NOT NULL CHECK (latest_version >= 1),
            published_version  integer CHECK (published_version >= 1),
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CHECK (published_version IS NULL OR published_version <= latest_version)
        )
        """
    )
    op.execute("CREATE INDEX agents_tenant_updated ON agents.agents (tenant_id, updated_at DESC)")
    _enable_rls("agents.agents")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON agents.agents TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE agents.agent_versions (
            agent_id     uuid NOT NULL REFERENCES agents.agents(id),
            tenant_id    text NOT NULL REFERENCES identity.tenants(id),
            version      integer NOT NULL CHECK (version >= 1),
            spec         jsonb NOT NULL,
            created_by   text NOT NULL,
            created_via  text NOT NULL CHECK (created_via IN ('form', 'api', 'architect', 'cli')),
            change_note  text NOT NULL DEFAULT '',
            created_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (agent_id, version)
        )
        """
    )
    _enable_rls("agents.agent_versions")
    # Version là immutable: chỉ đọc và thêm.
    op.execute(f"GRANT SELECT, INSERT ON agents.agent_versions TO {APP_ROLE}")

    # ------------------------------------------------------------- conversations
    op.execute("CREATE SCHEMA conversations")
    op.execute(f"GRANT USAGE ON SCHEMA conversations TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE conversations.threads (
            id             uuid PRIMARY KEY,
            tenant_id      text NOT NULL REFERENCES identity.tenants(id),
            user_id        text NOT NULL,
            agent_id       uuid NOT NULL REFERENCES agents.agents(id),
            agent_version  integer NOT NULL,
            is_draft       boolean NOT NULL DEFAULT false,
            title          text NOT NULL DEFAULT '',
            created_at     timestamptz NOT NULL DEFAULT now(),
            updated_at     timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX threads_user_updated ON conversations.threads "
        "(tenant_id, user_id, updated_at DESC)"
    )
    _enable_rls("conversations.threads")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON conversations.threads TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE conversations.runs (
            id                  uuid PRIMARY KEY,
            tenant_id           text NOT NULL REFERENCES identity.tenants(id),
            thread_id           uuid NOT NULL REFERENCES conversations.threads(id),
            agent_id            uuid NOT NULL REFERENCES agents.agents(id),
            agent_version       integer NOT NULL,
            user_id             text NOT NULL,
            model               text NOT NULL,
            status              text NOT NULL
                                CHECK (status IN
                                    ('running','completed','failed','refused','cancelled')),
            input_tokens        integer NOT NULL DEFAULT 0,
            output_tokens       integer NOT NULL DEFAULT 0,
            cache_read_tokens   integer NOT NULL DEFAULT 0,
            cache_write_tokens  integer NOT NULL DEFAULT 0,
            cost_usd            numeric(12, 6) NOT NULL DEFAULT 0,
            error               text,
            started_at          timestamptz NOT NULL DEFAULT now(),
            finished_at         timestamptz
        )
        """
    )
    op.execute(
        "CREATE INDEX runs_agent_started ON conversations.runs (tenant_id, agent_id, started_at)"
    )
    _enable_rls("conversations.runs")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON conversations.runs TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE conversations.messages (
            id          bigserial PRIMARY KEY,
            tenant_id   text NOT NULL REFERENCES identity.tenants(id),
            thread_id   uuid NOT NULL REFERENCES conversations.threads(id),
            run_id      uuid REFERENCES conversations.runs(id),
            role        text NOT NULL CHECK (role IN ('user', 'assistant')),
            content     jsonb NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX messages_thread ON conversations.messages (thread_id, id)")
    _enable_rls("conversations.messages")
    # Lịch sử append-only: sửa tin nhắn cũ làm hỏng thinking block và prompt cache (ADR-0014).
    op.execute(f"GRANT SELECT, INSERT ON conversations.messages TO {APP_ROLE}")
    op.execute(f"GRANT USAGE ON SEQUENCE conversations.messages_id_seq TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS conversations CASCADE")
    op.execute("DROP SCHEMA IF EXISTS agents CASCADE")
