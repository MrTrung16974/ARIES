"""MCP bridge: MCP server do Admin đăng ký (ADR-0018)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiagent_app"
TENANT_PREDICATE = "tenant_id = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.execute("CREATE SCHEMA mcp")
    op.execute(f"GRANT USAGE ON SCHEMA mcp TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE mcp.servers (
            id              uuid PRIMARY KEY,
            tenant_id       text NOT NULL REFERENCES identity.tenants(id),
            name            text NOT NULL,
            slug            text NOT NULL CHECK (slug ~ '^[a-z][a-z0-9_]{1,30}$'),
            transport       text NOT NULL CHECK (transport IN ('http', 'stdio')),
            url             text,
            command_key     text,
            secret_keys     jsonb NOT NULL DEFAULT '[]'::jsonb,
            enabled         boolean NOT NULL DEFAULT true,
            deleted         boolean NOT NULL DEFAULT false,
            tools           jsonb NOT NULL DEFAULT '[]'::jsonb,
            risk_overrides  jsonb NOT NULL DEFAULT '{}'::jsonb,
            last_sync_at    timestamptz,
            last_error      text,
            created_by      text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CHECK ((transport = 'http') = (url IS NOT NULL)),
            CHECK ((transport = 'stdio') = (command_key IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX mcp_servers_slug ON mcp.servers (tenant_id, slug) WHERE NOT deleted"
    )
    op.execute("ALTER TABLE mcp.servers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mcp.servers FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON mcp.servers "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON mcp.servers TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS mcp CASCADE")
