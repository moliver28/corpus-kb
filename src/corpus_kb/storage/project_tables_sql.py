"""The raw-table DDL fragments (Track C1, split for the 250-line rule).

Column definitions and the 022-convention RLS statement builder for the
per-project raw tables; ``storage/project_tables.py`` executes them. The
fragments carry no behavior and touch no database.
"""

from __future__ import annotations

from corpus_kb.storage.sql_ident import quoted_identifier

__all__ = ["raw_columns_for", "rls_statements"]


def raw_columns_for(backend: str) -> str:
    """The column block for one backend: ``postgres`` or ``duckdb``."""
    return _RAW_COLUMNS_PG if backend == "postgres" else _RAW_COLUMNS_DUCKDB


_RAW_COLUMNS_PG = """
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL,
    project_id UUID NOT NULL,
    source_path TEXT,
    source_hash CHAR(64) NOT NULL,
    content_type TEXT,
    raw_bytes BYTEA,
    raw_text TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source_hash)
"""

_RAW_COLUMNS_DUCKDB = """
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR NOT NULL,
    project_id VARCHAR NOT NULL,
    source_path VARCHAR,
    source_hash VARCHAR NOT NULL,
    content_type VARCHAR,
    raw_bytes BLOB,
    raw_text VARCHAR,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source_hash)
"""


def rls_statements(table: str) -> tuple[str, ...]:
    """The 022-convention tenant RLS policy for one runtime table."""
    quoted = quoted_identifier(table)
    return (
        f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS raw_table_tenant_isolation ON {quoted}",
        f"CREATE POLICY raw_table_tenant_isolation ON {quoted} "
        f"USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::UUID) "
        f"WITH CHECK (tenant_id = "
        f"NULLIF(current_setting('app.current_tenant_id', true), '')::UUID)",
        f"ALTER TABLE {quoted} FORCE ROW LEVEL SECURITY",
    )
