"""Knowledge base (RAG): kb, documents, chunks (pgvector + full-text) — ADR-0019

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
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
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE SCHEMA knowledge")
    op.execute(f"GRANT USAGE ON SCHEMA knowledge TO {APP_ROLE}")
    # unaccent() không IMMUTABLE → bọc lại để dùng trong cột sinh (tiếng Việt: bỏ dấu, đ → d).
    op.execute(
        """
        CREATE FUNCTION knowledge.f_unaccent(text) RETURNS text
        LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT
        AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
        """
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION knowledge.f_unaccent(text) TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE knowledge.kbs (
            id                   uuid PRIMARY KEY,
            tenant_id            text NOT NULL REFERENCES identity.tenants(id),
            name                 text NOT NULL,
            description          text NOT NULL DEFAULT '',
            scope                text NOT NULL CHECK (scope IN ('personal', 'workspace')),
            owner_id             text NOT NULL,
            data_classification  text NOT NULL CHECK (data_classification IN
                                   ('public', 'internal', 'confidential', 'restricted')),
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, name)
        )
        """
    )
    _enable_rls("knowledge.kbs")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge.kbs TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE knowledge.documents (
            id            uuid PRIMARY KEY,
            tenant_id     text NOT NULL REFERENCES identity.tenants(id),
            kb_id         uuid NOT NULL REFERENCES knowledge.kbs(id) ON DELETE CASCADE,
            filename      text NOT NULL,
            content_type  text NOT NULL,
            size_bytes    integer NOT NULL CHECK (size_bytes >= 0),
            sha256        text NOT NULL,
            status        text NOT NULL CHECK (status IN
                            ('pending', 'processing', 'ready', 'failed')),
            error         text,
            title         text NOT NULL DEFAULT '',
            created_by    text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (kb_id, sha256)
        )
        """
    )
    op.execute("CREATE INDEX documents_kb ON knowledge.documents (kb_id, created_at DESC)")
    _enable_rls("knowledge.documents")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge.documents TO {APP_ROLE}")

    op.execute(
        """
        CREATE TABLE knowledge.chunks (
            id           uuid PRIMARY KEY,
            tenant_id    text NOT NULL REFERENCES identity.tenants(id),
            kb_id        uuid NOT NULL REFERENCES knowledge.kbs(id) ON DELETE CASCADE,
            document_id  uuid NOT NULL REFERENCES knowledge.documents(id) ON DELETE CASCADE,
            ord          integer NOT NULL,
            heading      text NOT NULL DEFAULT '',
            page         integer,
            text         text NOT NULL,
            embedding    vector(1024) NOT NULL,
            tsv          tsvector GENERATED ALWAYS AS
                           (to_tsvector('simple', knowledge.f_unaccent(heading || ' ' || text)))
                           STORED
        )
        """
    )
    op.execute("CREATE INDEX chunks_doc ON knowledge.chunks (document_id, ord)")
    op.execute("CREATE INDEX chunks_kb ON knowledge.chunks (kb_id)")
    op.execute("CREATE INDEX chunks_tsv ON knowledge.chunks USING gin (tsv)")
    op.execute(
        "CREATE INDEX chunks_embedding ON knowledge.chunks USING hnsw (embedding vector_cosine_ops)"
    )
    _enable_rls("knowledge.chunks")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON knowledge.chunks TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS knowledge CASCADE")
