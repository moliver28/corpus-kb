"""Meta-embedding upsert (Living Corpus W6).

Idempotent per (tenant, meta_kind, meta_key): the built_at stamp and
the embedding always reflect the last build. One INSERT .. ON CONFLICT
per row, explicit positional args (the store convention), inside one
tenant-scoped transaction. The search-side nearest-neighbour reads live
with each engine's search backend (the dialects differ); this module is
engine-neutral writing only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from corpus_kb.analysis.meta_build import MetaRow

if TYPE_CHECKING:
    import asyncpg

UPSERT_SQL = (
    "INSERT INTO meta_embeddings"
    " (tenant_id, meta_kind, meta_key, label, embedding, source_count)"
    " VALUES ($1, $2, $3, $4, $5::halfvec, $6)"
    " ON CONFLICT (tenant_id, meta_kind, meta_key) DO UPDATE SET"
    " label = EXCLUDED.label, embedding = EXCLUDED.embedding,"
    " source_count = EXCLUDED.source_count, built_at = NOW()"
)


async def upsert_meta_rows(pool: asyncpg.Pool, tenant_id: str, rows: list[MetaRow]) -> int:
    """Write the built rows; returns the count written."""
    from corpus_kb.storage.tenant_conn import tenant_connection

    if not rows:
        return 0
    async with tenant_connection(pool, tenant_id) as conn, conn.transaction():
        for row in rows:
            literal = "[" + ",".join(repr(float(v)) for v in row.vector) + "]"
            await conn.execute(
                UPSERT_SQL,
                tenant_id,
                row.meta_kind,
                row.meta_key,
                row.label,
                literal,
                row.source_count,
            )
    return len(rows)
