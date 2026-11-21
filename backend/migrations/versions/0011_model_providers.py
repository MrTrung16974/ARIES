"""Nhà cung cấp LLM động (Gemini, Ollama, OpenAI, Claude, Groq, OpenRouter, DeepSeek)

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "aiagent_app"
TENANT_PREDICATE = "tenant_id = current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.execute("CREATE SCHEMA llm")
    op.execute(f"GRANT USAGE ON SCHEMA llm TO {APP_ROLE}")
    op.execute(
        """
        CREATE TABLE llm.providers (
            tenant_id   text NOT NULL REFERENCES identity.tenants(id),
            provider_id text NOT NULL,
            enabled     boolean NOT NULL DEFAULT true,
            base_url    text,
            models      jsonb NOT NULL DEFAULT '[]'::jsonb,
            extra       jsonb NOT NULL DEFAULT '{}'::jsonb,
            has_api_key boolean NOT NULL DEFAULT false,
            updated_by  text NOT NULL,
            updated_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, provider_id)
        )
        """
    )
    table = "llm.providers"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS llm CASCADE")
