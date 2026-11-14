"""The journey build-state readers (novice-UX program W3).

One read-only connection per doctor run fills :class:`JourneyState`.
The probed matrix (register input -> state key -> probed as):

=============== =============== =================================================
Register input  State key       Probed as
=============== =============== =================================================
raw_text        chunks          rows in ``chunks``
embeddings      vectors         ``chunks_vectors`` rows, not degraded, not NULL
labels          labels          rows in ``chunk_labels``
predictions     predictions     rows in ``chunk_predictions``
topics build    topics          rows in ``topics``
timestamps      dated_documents ``documents.document_date`` not NULL
=============== =============== =================================================

``llm`` rides the doctor's own Ollama probe; ``seed_labels`` and
``ontology`` stay unprobed — the user brings them per call. A missing
table means the migrations never ran: the reader answers ``None`` and
the journey reports one honest omitted check instead of a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The tables the journey counts. Any missing table means the migrations
# never ran.
_JOURNEY_TABLES = ("chunks", "chunks_vectors", "chunk_labels", "chunk_predictions", "topics")


@dataclass(frozen=True)
class JourneyState:
    """The probed build state the journey matrix reads."""

    chunks: int
    vectors: int
    labels: int
    predictions: int
    topics: int
    dated_documents: int


async def _pg_build_state(conn: Any) -> JourneyState | None:
    """Counts under the default-tenant GUC (FORCE RLS zeroes a bare read)."""
    from corpus_kb.contracts.model import DEFAULT_TENANT_ID

    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)", str(DEFAULT_TENANT_ID)
        )
        for table in _JOURNEY_TABLES:
            if await conn.fetchval(f"SELECT to_regclass('public.{table}')") is None:
                return None
        has_degraded = await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'chunks_vectors' AND column_name = 'degraded'"
        )
        vectors_sql = (
            "SELECT count(*) FROM chunks_vectors WHERE embedding IS NOT NULL AND degraded = FALSE"
            if has_degraded
            else "SELECT count(*) FROM chunks_vectors WHERE embedding IS NOT NULL"
        )
        return JourneyState(
            chunks=int(await conn.fetchval("SELECT count(*) FROM chunks") or 0),
            vectors=int(await conn.fetchval(vectors_sql) or 0),
            labels=int(await conn.fetchval("SELECT count(*) FROM chunk_labels") or 0),
            predictions=int(await conn.fetchval("SELECT count(*) FROM chunk_predictions") or 0),
            topics=int(await conn.fetchval("SELECT count(*) FROM topics") or 0),
            dated_documents=int(
                await conn.fetchval(
                    "SELECT count(*) FROM documents WHERE document_date IS NOT NULL"
                )
                or 0
            ),
        )


def _scalar(con: Any, sql: str) -> int:
    """One single-value read, honest zero when the row is missing."""
    row = con.execute(sql).fetchone()
    return int(row[0]) if row else 0


def _duckdb_build_state(conn_str: str) -> JourneyState | None:
    """Counts over one read-only DuckDB connection (single-user file)."""
    import duckdb

    from corpus_kb.storage.duckdb_support import duckdb_conn_string_to_path

    con = duckdb.connect(duckdb_conn_string_to_path(conn_str), read_only=True)
    try:
        for table in _JOURNEY_TABLES:
            row = con.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_name = ?",
                [table],
            ).fetchone()
            if not row or not row[0]:
                return None
        has_degraded = con.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = 'chunks_vectors' "
            "AND column_name = 'degraded'"
        ).fetchone()
        vectors_sql = (
            "SELECT COUNT(*) FROM chunks_vectors WHERE embedding IS NOT NULL AND NOT degraded"
            if has_degraded and has_degraded[0]
            else "SELECT COUNT(*) FROM chunks_vectors WHERE embedding IS NOT NULL"
        )
        dated = con.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = 'documents' "
            "AND column_name = 'document_date'"
        ).fetchone()
        dated_sql = (
            "SELECT COUNT(*) FROM documents WHERE document_date IS NOT NULL"
            if dated and dated[0]
            else "SELECT 0"
        )
        return JourneyState(
            chunks=_scalar(con, "SELECT COUNT(*) FROM chunks"),
            vectors=_scalar(con, vectors_sql),
            labels=_scalar(con, "SELECT COUNT(*) FROM chunk_labels"),
            predictions=_scalar(con, "SELECT COUNT(*) FROM chunk_predictions"),
            topics=_scalar(con, "SELECT COUNT(*) FROM topics"),
            dated_documents=_scalar(con, dated_sql),
        )
    finally:
        con.close()


async def read_build_state(conn_str: str, backend: str) -> JourneyState | None:
    """One read-only state read on the configured backend (never raises)."""
    if backend == "duckdb":
        return _duckdb_build_state(conn_str)
    import asyncpg

    conn = await asyncpg.connect(conn_str, timeout=3)
    try:
        return await _pg_build_state(conn)
    finally:
        await conn.close()


__all__ = ["JourneyState", "read_build_state"]
