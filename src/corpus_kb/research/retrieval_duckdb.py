"""DuckDB retrieval twins (storage portability program §13, sp-research-port).

The Postgres arms in ``research/retrieval_arms.py`` are byte-identical
legacy SQL and are pinned by file-scan (tests/test_query_determinism.py
counts the score ORDER BY clauses there), so this module carries the
DuckDB forms instead:

- DENSE: always the exact cosine scan (DuckDB has no ANN index; the U40
  relaxed-ordering shape — candidate set inside the CTE, exact ordering
  outside — is preserved).
- LEXICAL: the ``fts_main_<table>.match_bm25`` scalar macro over the
  managed research FTS indexes (``research/fts_duckdb.py``); NULL-score
  rows drop, ties break on the stable id ASC.
- FUSION: the pure Python ``rrf_fuse`` (handlers/query_fusion.py — the
  exact port of migration 024's ``corpus.rrf_fusion``), since the gateway
  rejects ``corpus.``-qualified SQL.

Every ordering repeats the total order fscore/score DESC, id ASC
(zero-drift rule 9).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS
from corpus_kb.handlers.query_fusion import RowLike, rrf_fuse
from corpus_kb.research.retrieval import (
    _exchange_filters,
    _exchange_payload,
    _Sql,
    _unit_filters,
    _unit_payload,
)
from corpus_kb.storage.dialect import sanitize_fts_query

# The research vectors are exactly 1024 dims (the D6 contract); the probe
# literal rides as the compact varchar (vector_literal) plus the cast.
_DENSE_UNIT_SQL = """
                WITH candidate AS MATERIALIZED (
                    SELECT u.unit_id, u.doc_id, u.exchange_id, u.seq, u.text, u.role_in_exchange,
                           1 - array_cosine_distance(u.embedding, {vec}::FLOAT[1024]) AS score
                    FROM research_units u
                    JOIN documents d ON d.doc_id = u.doc_id
                    LEFT JOIN research_speakers s ON s.speaker_id = u.speaker_id
                    WHERE u.tenant_id = $1 AND d.project_id = $2
                      AND u.embedding IS NOT NULL{filters}
                )
                SELECT candidate.unit_id, candidate.doc_id, candidate.exchange_id,
                       candidate.seq, candidate.text, candidate.role_in_exchange,
                       candidate.score + 0 AS score
                FROM candidate
                ORDER BY candidate.score + 0 DESC, candidate.unit_id ASC
                LIMIT {limit}
                """

_DENSE_EXCHANGE_SQL = """
                WITH candidate AS MATERIALIZED (
                    SELECT e.exchange_id, e.doc_id, e.seq, coalesce(e.qa_text, '') AS text,
                           1 - array_cosine_distance(e.embedding, {vec}::FLOAT[1024]) AS score
                    FROM research_exchanges e
                    JOIN documents d ON d.doc_id = e.doc_id
                    WHERE e.tenant_id = $1 AND d.project_id = $2
                      AND e.embedding IS NOT NULL{filters}
                )
                SELECT candidate.exchange_id, candidate.doc_id, candidate.seq,
                       candidate.text, candidate.score + 0 AS score
                FROM candidate
                ORDER BY candidate.score + 0 DESC, candidate.exchange_id ASC
                LIMIT {limit}
                """

_FTS_UNIT_SQL = """
                SELECT scored.unit_id, scored.doc_id, scored.exchange_id, scored.seq,
                       scored.text, scored.role_in_exchange, scored.score
                FROM (
                    SELECT u.unit_id, u.doc_id, u.exchange_id, u.seq, u.text,
                           u.role_in_exchange,
                           fts_main_research_units.match_bm25(u.unit_id, {tsq}) AS score
                    FROM research_units u
                    JOIN documents d ON d.doc_id = u.doc_id
                    LEFT JOIN research_speakers s ON s.speaker_id = u.speaker_id
                    WHERE u.tenant_id = $1 AND d.project_id = $2{filters}
                ) scored
                WHERE scored.score IS NOT NULL
                ORDER BY scored.score DESC, scored.unit_id ASC
                LIMIT {limit}
                """

_FTS_EXCHANGE_SQL = """
                SELECT scored.exchange_id, scored.doc_id, scored.seq, scored.text,
                       scored.score
                FROM (
                    SELECT e.exchange_id, e.doc_id, e.seq, coalesce(e.qa_text, '') AS text,
                           fts_main_research_exchanges.match_bm25(e.exchange_id, {tsq}) AS score
                    FROM research_exchanges e
                    JOIN documents d ON d.doc_id = e.doc_id
                    WHERE e.tenant_id = $1 AND d.project_id = $2
                      AND coalesce(e.qa_text, '') <> ''{filters}
                ) scored
                WHERE scored.score IS NOT NULL
                ORDER BY scored.score DESC, scored.exchange_id ASC
                LIMIT {limit}
                """

# The dense arms pin the research width in SQL text (the cast must match
# the FLOAT[k] column). These are .format(vec=, limit=, filters=)
# templates, so the width cannot ride an f-string; the asserts tie the
# pinned width to the shared constant, and a width change fails here at
# import instead of per query.
assert f"FLOAT[{RESEARCH_EMBEDDING_DIMENSIONS}]" in _DENSE_UNIT_SQL
assert f"FLOAT[{RESEARCH_EMBEDDING_DIMENSIONS}]" in _DENSE_EXCHANGE_SQL


async def unit_arms_duck(
    conn: Any, query_vector: list[float] | None, q: Any, depth: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """DuckDB dense + lexical arms over research_units (see module docstring)."""
    from corpus_kb.storage.duckdb_support import vector_literal

    dense: list[dict[str, Any]] = []
    if query_vector is not None:
        sql = _Sql(str(q.tenant_id), str(q.project_id))
        vec = sql.p(vector_literal(query_vector))
        limit = sql.p(depth)
        filters = _unit_filters(sql, q, duck=True)
        rows = await conn.fetch(
            _DENSE_UNIT_SQL.format(vec=vec, limit=limit, filters=filters), *sql.values
        )
        dense = [_unit_payload(r) for r in rows]

    sql = _Sql(str(q.tenant_id), str(q.project_id))
    tsq = sql.p(sanitize_fts_query(q.query))
    limit = sql.p(depth)
    filters = _unit_filters(sql, q, duck=True)
    rows = await conn.fetch(
        _FTS_UNIT_SQL.format(tsq=tsq, limit=limit, filters=filters), *sql.values
    )
    return dense, [_unit_payload(r) for r in rows]


async def exchange_arms_duck(
    conn: Any, query_vector: list[float] | None, q: Any, depth: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """DuckDB dense + lexical arms over research_exchanges."""
    from corpus_kb.storage.duckdb_support import vector_literal

    dense: list[dict[str, Any]] = []
    if query_vector is not None:
        sql = _Sql(str(q.tenant_id), str(q.project_id))
        vec = sql.p(vector_literal(query_vector))
        limit = sql.p(depth)
        filters = _exchange_filters(sql, q, duck=True)
        rows = await conn.fetch(
            _DENSE_EXCHANGE_SQL.format(vec=vec, limit=limit, filters=filters), *sql.values
        )
        dense = [_exchange_payload(r) for r in rows]

    sql = _Sql(str(q.tenant_id), str(q.project_id))
    tsq = sql.p(sanitize_fts_query(q.query))
    limit = sql.p(depth)
    filters = _exchange_filters(sql, q, duck=True)
    rows = await conn.fetch(
        _FTS_EXCHANGE_SQL.format(tsq=tsq, limit=limit, filters=filters), *sql.values
    )
    return dense, [_exchange_payload(r) for r in rows]


def rrf_pure(
    dense: list[dict[str, Any]], lexical: list[dict[str, Any]], depth: int, rrf_k: int
) -> list[dict[str, Any]]:
    """The pure-Python fusion twin of retrieval._rrf (migration-024 order)."""
    if not dense and not lexical:
        return []
    by_uuid = {str(row["chunk_id"]): row for row in dense + lexical}
    fused: list[dict[str, Any]] = []
    lex_rows: Sequence[RowLike] = cast("Sequence[RowLike]", lexical)
    dense_rows: Sequence[RowLike] = cast("Sequence[RowLike]", dense)
    for row in rrf_fuse(lex_rows, dense_rows, k=depth, rrf_k=rrf_k):
        item: dict[str, Any] = {
            "chunk_id": str(row.chunk_id),
            "text": row.text,
            "source": "",
            "doc_id": str(row.doc_id),
            "score": float(row.score),
        }
        meta = by_uuid.get(str(row.chunk_id))
        if meta is not None:
            for name in ("_key", "_kind", "_unit_id", "_exchange_id", "_seq", "_doc_id"):
                if name in meta:
                    item[name] = meta[name]
        fused.append(item)
    # Zero-drift determinism (rule 9): re-assert fscore DESC, chunk id ASC.
    fused.sort(key=lambda item: (-float(item["score"]), str(item["chunk_id"])))
    return fused
