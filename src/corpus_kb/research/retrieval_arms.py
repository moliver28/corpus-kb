"""Shared unit/exchange retrieval arm builder (issue 232; portability §13).

One arm builder (:func:`_child_arms`) serves both retrieval grains — the
unit grain (``research_units``) and the exchange grain
(``research_exchanges``). The per-grain SQL pieces (aliases, joins, select
lists, FTS text expression, filter fragments) come from
``research/retrieval_filters.py``; this module builds the dense and FTS
SQL around them. The generated SQL keeps the former shape: ``$1`` tenant,
``$2`` project, filters ``$3+``, then the query vector and the candidate
limit. Row shaping (``_child_payload``, ``_children_from_fused``) lives
here too — the write side of the child-row contract.

The DuckDB twins live in ``research/retrieval_duckdb.py`` (no pgvector,
no SQL fusion); this module dispatches to them on the ``duck`` flag.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid5

from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS
from corpus_kb.research.citations import _Child
from corpus_kb.research.conn import SqlConn
from corpus_kb.research.retrieval_filters import (
    _EXCHANGE_GRAIN,
    _UNIT_GRAIN,
    _Grain,
    _row_filters,
    _Sql,
)
from corpus_kb.research.retrieval_settings import (
    DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
    SelectivityReader,
    estimate_selectivity,
    should_use_exact_scan,
)

if TYPE_CHECKING:
    from corpus_kb.research.retrieval import ResearchQuery

# The dense-arm halfvec cast, interpolated into the f-string arms below so
# the index cast and the query probe share the research width constant.
_HALFVEC = f"::halfvec({RESEARCH_EMBEDDING_DIMENSIONS})"

# U21: paired-count selectivity probe, same join shape + parameter
# numbering as the dense arms ($1 tenant, $2 project, filters $3+).
_COUNT_SQL = """
SELECT count(*)::float8 AS total,
       count(*) FILTER (WHERE TRUE {filters})::float8 AS matched
FROM {table}
{joins}
WHERE {al}.tenant_id = $1 AND d.project_id = $2 AND {al}.embedding IS NOT NULL
"""

# Identity of a retrieval child: uuid5 over a fixed namespace, so chunk ids
# are stable across runs (zero-drift rule 9).
_RRF_NAMESPACE = UUID("6f6f6f6f-6f6f-6f6f-6f6f-6f6f6f6f6f6f")


def _child_uuid(key: str) -> str:
    return str(uuid5(_RRF_NAMESPACE, f"corpus-research:{key}"))


async def _child_arms(
    conn: SqlConn,
    query_vector: list[float] | None,
    q: ResearchQuery,
    depth: int,
    grain: _Grain,
    exact_threshold: float = DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
    duck: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Dense + FTS arms for one grain — the ONE unit/exchange builder.

    Postgres path: the U21 selectivity probe runs once when filters exist;
    below the threshold the exact scan drops the inner ordering, so the
    candidate set is the complete filter match. Otherwise (U40) the ANN
    scan stays inside the MATERIALIZED CTE and the exact ordering applies
    outside (``score + 0`` defeats planner sort-elision). Every ordering
    repeats fused score DESC, id ASC (zero-drift rule 9).
    """
    if duck:
        from corpus_kb.research.retrieval_duckdb import exchange_arms_duck, unit_arms_duck

        duck_arms = unit_arms_duck if grain is _UNIT_GRAIN else exchange_arms_duck
        return await duck_arms(conn, query_vector, q, depth)
    dense_sql = _Sql(str(q.tenant_id), str(q.project_id))
    filters = _row_filters(dense_sql, q, grain)
    dense: list[dict[str, Any]] = []
    if query_vector is not None:
        vec = dense_sql.p(str(query_vector))
        limit = dense_sql.p(depth)
        # U21: probe matched/total once; a selective filter can starve the
        # ordered HNSW scan of candidates.
        selectivity = (
            await estimate_selectivity(
                cast("SelectivityReader", conn),
                _COUNT_SQL.format(
                    al=grain.alias, filters=filters, joins=grain.joins, table=grain.table
                ),
                list(dense_sql.values),
            )
            if filters
            else None
        )
        exact = should_use_exact_scan(bool(filters), selectivity, exact_threshold)
        inner = (
            ""
            if exact
            else f"ORDER BY {grain.embedding}{_HALFVEC} <=> {vec}{_HALFVEC}\nLIMIT {limit}"
        )
        outer = f"LIMIT {limit}" if exact else ""
        rows = await conn.fetch(
            f"""
            WITH candidate AS MATERIALIZED (
                SELECT {grain.cols},
                       1 - ({grain.embedding}{_HALFVEC} <=> {vec}{_HALFVEC}) AS score
                FROM {grain.table}
                {grain.joins}
                WHERE {grain.alias}.tenant_id = $1 AND d.project_id = $2
                  AND {grain.embedding} IS NOT NULL{filters}
                {inner}
            )
            SELECT {grain.outer_cols},
                   candidate.score + 0 AS score
            FROM candidate
            ORDER BY candidate.score + 0 DESC, candidate.{grain.row_id} ASC
            {outer}
            """,
            *dense_sql.values,
        )
        dense = [_child_payload(r, grain) for r in rows]

    fts_sql = _Sql(str(q.tenant_id), str(q.project_id))
    fts_filters = _row_filters(fts_sql, q, grain)
    tsq = fts_sql.p(q.query)
    limit = fts_sql.p(depth)
    rows = await conn.fetch(
        f"""
        SELECT {grain.cols},
               ts_rank(to_tsvector('english', {grain.text_expr}),
                       plainto_tsquery('english', {tsq})) AS score
        FROM {grain.table}
        {grain.joins}
        WHERE {grain.alias}.tenant_id = $1 AND d.project_id = $2{grain.fts_guard}{fts_filters}
          AND to_tsvector('english', {grain.text_expr}) @@ plainto_tsquery('english', {tsq})
        ORDER BY score DESC, {grain.alias}.{grain.row_id} ASC
        LIMIT {limit}
        """,
        *fts_sql.values,
    )
    return dense, [_child_payload(r, grain) for r in rows]


async def _unit_arms(
    conn: SqlConn,
    query_vector: list[float] | None,
    q: ResearchQuery,
    depth: int,
    exact_threshold: float = DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
    duck: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Unit-grain arms (see :func:`_child_arms`)."""
    return await _child_arms(conn, query_vector, q, depth, _UNIT_GRAIN, exact_threshold, duck)


async def _exchange_arms(
    conn: SqlConn,
    query_vector: list[float] | None,
    q: ResearchQuery,
    depth: int,
    exact_threshold: float = DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
    duck: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Exchange-grain arms (see :func:`_child_arms`)."""
    return await _child_arms(conn, query_vector, q, depth, _EXCHANGE_GRAIN, exact_threshold, duck)


def _child_payload(row: Mapping[str, Any], grain: _Grain) -> dict[str, Any]:
    """One arm row mapped to the fused-child shape (shared by both grains)."""
    rid = int(row[grain.row_id])
    return {
        "chunk_id": _child_uuid(f"{grain.key}:{rid}"),
        "text": str(row["text"]),
        "source": "",
        "doc_id": str(row["doc_id"]),
        "score": float(row["score"]),
        "_key": f"{grain.key}:{rid}",
        "_kind": grain.kind(row),
        "_unit_id": grain.unit_of(rid, row),
        "_exchange_id": grain.exchange_of(rid, row),
        "_seq": int(row["seq"]),
        "_doc_id": str(row["doc_id"]),
    }


def _unit_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Unit-grain payload (the DuckDB twin binds this name)."""
    return _child_payload(row, _UNIT_GRAIN)


def _exchange_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    """Exchange-grain payload (same binding as _unit_payload)."""
    return _child_payload(row, _EXCHANGE_GRAIN)


def _children_from_fused(fused: list[dict[str, Any]]) -> list[_Child]:
    """Fused child rows back to :class:`_Child` (inverse of _child_payload)."""
    children: list[_Child] = []
    for row in fused:
        if "_key" not in row:
            continue
        children.append(
            _Child(
                key=str(row["_key"]),
                kind=str(row.get("_kind", "")),
                doc_id=str(row.get("_doc_id", row["doc_id"])),
                unit_id=row.get("_unit_id"),
                exchange_id=row.get("_exchange_id"),
                seq=int(row.get("_seq", 0)),
                text=str(row["text"]),
                score=float(row["score"]),
            )
        )
    return children
