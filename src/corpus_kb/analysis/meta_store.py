"""Meta-embedding upsert (Living Corpus W6).

Idempotent per (tenant, meta_kind, meta_key): the built_at stamp and
the embedding always reflect the last build. One INSERT .. ON CONFLICT
per row, explicit positional args (the store convention), inside one
tenant-scoped transaction (tenant_connection handles both engines).
The search-side nearest-neighbour reads live with each engine's search
backend (the dialects differ); this module is the single writer.
"""

from __future__ import annotations

from typing import Any

from corpus_kb.analysis.meta_build import MetaRow
from corpus_kb.storage.gateway import SqlConn, is_gateway

UPSERT_SQL = (
    "INSERT INTO meta_embeddings"
    " (tenant_id, meta_kind, meta_key, label, embedding, source_count)"
    " VALUES ($1, $2, $3, $4, $5::halfvec, $6)"
    " ON CONFLICT (tenant_id, meta_kind, meta_key) DO UPDATE SET"
    " label = EXCLUDED.label, embedding = EXCLUDED.embedding,"
    " source_count = EXCLUDED.source_count, built_at = NOW()"
)

# DuckDB twin column is FLOAT[2560]: the vector binds as a plain Python
# list and the halfvec cast must not appear.
UPSERT_SQL_DUCK = (
    "INSERT INTO meta_embeddings"
    " (tenant_id, meta_kind, meta_key, label, embedding, source_count)"
    " VALUES ($1, $2, $3, $4, $5, $6)"
    " ON CONFLICT (tenant_id, meta_kind, meta_key) DO UPDATE SET"
    " label = EXCLUDED.label, embedding = EXCLUDED.embedding,"
    " source_count = EXCLUDED.source_count, built_at = NOW()"
)


def _halfvec_literal(vector: list[float]) -> str:
    """pgvector text form of one vector (repr keeps full precision)."""
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


async def upsert_meta_rows(
    pool: Any,
    tenant_id: str,
    rows: list[MetaRow],
    *,
    is_duckdb: bool = False,
) -> int:
    """Write the built rows; returns the count written."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    if not rows:
        return 0
    sql = UPSERT_SQL_DUCK if is_duckdb else UPSERT_SQL
    async with tenant_connection(pool, tenant_id) as conn:
        if is_gateway(conn):
            async with conn.transaction(tenant_id):
                await _write_rows(conn, sql, tenant_id, rows, is_duckdb=is_duckdb)
        else:
            async with conn.transaction():
                await _write_rows(conn, sql, tenant_id, rows, is_duckdb=is_duckdb)
    return len(rows)


async def _write_rows(
    conn: SqlConn, sql: str, tenant_id: str, rows: list[MetaRow], *, is_duckdb: bool
) -> None:
    for row in rows:
        embedding: Any = list(row.vector) if is_duckdb else _halfvec_literal(row.vector)
        await conn.execute(
            sql,
            tenant_id,
            row.meta_kind,
            row.meta_key,
            row.label,
            embedding,
            row.source_count,
        )
