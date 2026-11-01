"""Parent-child retrieval over the research read models (todo 12, v5 §6).

Hybrid dense + lexical over retrieval CHILDREN (v5 §5.2: units of kind
answer/question, exchanges of kind qa), fused with the house RRF function
(``corpus.rrf_fusion``, k from ``search.rrf_k`` — default 60, migration 006
precedent), optionally cross-encoder reranked, then mapped to PARENTS (the
exchange, or the sliding window for facilitator-less sources): search
children, return the parent exchange with the question above and the answer
highlighted.

Every query is scoped by tenant (RLS) AND ``project_id``; optional filters:
``source_type`` (documents.source_type), ``speaker_role``, ``doc_ids``,
``topic_id``. Guide-item scoping is DEFERRED (r9: no filter over an
unpopulated column).

QUERY-side embeddings route through ``instruct()`` (Wave-1 Embedder
Protocol): the instruction prefix applies to the query text only; children
were embedded RAW + deterministic-prefix at projection time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import UUID, uuid5

import asyncpg

from corpus_kb.config import load_config
from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.research.chunking import CHILD_QA, unit_kind
from corpus_kb.research.citations import (
    Citation,
    _Child,
    _citations,
    _meeting_windows,
    _to_parents,
)
from corpus_kb.research.conn import SqlConn, Store, dialect_of, tenant_scope
from corpus_kb.research.fts_duckdb import ensure_research_fts
from corpus_kb.research.retrieval_settings import (
    DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
    RetrievalSettings,
    SelectivityReader,
    estimate_selectivity,
    load_retrieval_settings,
    should_use_exact_scan,
)
from corpus_kb.research.search_settings import (
    PGVECTOR_VERSION_SQL,
    HnswSettings,
    SqlExecutor,
    apply_hnsw_settings,
    load_hnsw_settings,
    missing_rrf_fusion_error,
    supports_iterative_scan,
)

logger = logging.getLogger(__name__)

CANDIDATE_MULTIPLIER = 8
RERANK_MIN_CANDIDATES = 30
_RRF_NAMESPACE = UUID("6f6f6f6f-6f6f-6f6f-6f6f-6f6f6f6f6f6f")

# The dense-arm halfvec cast, interpolated into the f-string arms below so
# the index cast and the query probe share the research width constant.
_HALFVEC = f"::halfvec({RESEARCH_EMBEDDING_DIMENSIONS})"

# U21: paired-count selectivity probes, same join shape + parameter
# numbering as the dense arms ($1 tenant, $2 project, filters $3+).
_UNIT_COUNT_SQL = """
SELECT count(*)::float8 AS total,
       count(*) FILTER (WHERE TRUE {filters})::float8 AS matched
FROM research_units u
JOIN documents d ON d.doc_id = u.doc_id
LEFT JOIN research_speakers s ON s.speaker_id = u.speaker_id
WHERE u.tenant_id = $1 AND d.project_id = $2 AND u.embedding IS NOT NULL
"""

_EXCHANGE_COUNT_SQL = """
SELECT count(*)::float8 AS total,
       count(*) FILTER (WHERE TRUE {filters})::float8 AS matched
FROM research_exchanges e
JOIN documents d ON d.doc_id = e.doc_id
WHERE e.tenant_id = $1 AND d.project_id = $2 AND e.embedding IS NOT NULL
"""

# Assignment-confidence floors (todo-18 notebook filter): --min-confidence X
# admits X and every STRONGER level.
CONFIDENCE_FLOORS: dict[str, tuple[str, ...]] = {
    "high": ("high",),
    "medium": ("high", "medium"),
    "low": ("high", "medium", "low"),
}


@dataclass(frozen=True)
class ResearchQuery:
    """A project-scoped retrieval query (tenant + project mandatory)."""

    query: str
    tenant_id: UUID
    project_id: UUID
    k: int = 10
    source_type: str | None = None
    speaker_role: str | None = None
    doc_ids: tuple[UUID, ...] | None = None
    topic_id: int | None = None
    code_id: str | None = None
    min_confidence: str | None = None
    review_status: str | None = None
    include_exchange_children: bool = True
    rerank: bool = True


class _Sql:
    """Numbered-parameter builder: every value gets a distinct placeholder."""

    def __init__(self, tenant_id: str, project_id: str) -> None:
        self.values: list[object] = [tenant_id, project_id]

    def p(self, value: object) -> str:
        self.values.append(value)
        return f"${len(self.values)}"


def _child_uuid(key: str) -> str:
    return str(uuid5(_RRF_NAMESPACE, f"corpus-research:{key}"))


async def ranked_children(
    store: Store,
    embedder: ResearchEmbedder,
    q: ResearchQuery,
    reranker: object | None = None,
    hnsw_settings: HnswSettings | None = None,
    retrieval_settings: RetrievalSettings | None = None,
) -> list[dict[str, Any]]:
    """Rank retrieval children; return the fused list BEFORE parent mapping.

    This is the ONE rank seam: dense + FTS arms fused by the house RRF
    inside one tenant-scoped transaction. The eval runner, the calibration
    sweeps and :func:`research_search` all rank through it, so the numbers
    they report are the same species by construction. ``retrieval_settings``
    drives the fusion constant (``rrf_k``) and floors the per-arm candidate
    depth (``vector_candidates`` / ``lexical_candidates``); ``reranker``
    reranks the children when given and ``q.rerank`` is set. Rows carry
    ``chunk_id``, ``score``, and fusion metadata; order is fused-score
    DESC, chunk id ASC (zero-drift rule 9). No truncation: callers cut to
    their own k AFTER any rerank.
    """
    from corpus_kb.rag.embedder import instruct

    config = load_config()
    r_settings = (
        retrieval_settings if retrieval_settings is not None else load_retrieval_settings(config)
    )
    depth = max(
        q.k * CANDIDATE_MULTIPLIER,
        RERANK_MIN_CANDIDATES,
        r_settings.vector_candidates,
        r_settings.lexical_candidates,
    )
    query_vector: list[float] | None = await embedder.embed_cached(q.tenant_id, instruct(q.query))
    settings = hnsw_settings if hnsw_settings is not None else load_hnsw_settings(config)
    duck = dialect_of(store).is_duckdb

    async with tenant_scope(store, q.tenant_id) as conn:
        if not duck:
            version_row = await conn.fetchrow(PGVECTOR_VERSION_SQL)
            extversion = None if version_row is None else str(version_row["extversion"])
            if supports_iterative_scan(extversion):
                await apply_hnsw_settings(cast("SqlExecutor", conn), settings)
        else:
            await ensure_research_fts(conn)
        dense_units, fts_units = await _unit_arms(
            conn, query_vector, q, depth, r_settings.exact_filter_selectivity_threshold, duck
        )
        dense_ex: list[dict[str, Any]] = []
        fts_ex: list[dict[str, Any]] = []
        if q.include_exchange_children:
            dense_ex, fts_ex = await _exchange_arms(
                conn, query_vector, q, depth, r_settings.exact_filter_selectivity_threshold, duck
            )

        units_fused = await _rrf(conn, dense_units, fts_units, depth, r_settings.rrf_k, duck)
        exch_fused = await _rrf(conn, dense_ex, fts_ex, depth, r_settings.rrf_k, duck)
        fused = await _rrf(conn, units_fused, exch_fused, depth, r_settings.rrf_k, duck)

    if reranker is not None and q.rerank and fused:
        fused = await _rerank_children(q.query, fused, reranker)
    return fused


async def research_search(
    store: Store,
    embedder: ResearchEmbedder,
    q: ResearchQuery,
    reranker: object | None = None,
    hnsw_settings: HnswSettings | None = None,
    retrieval_settings: RetrievalSettings | None = None,
) -> list[Citation]:
    """Search retrieval children, return parent citations (small-to-big).

    Ranking runs through :func:`ranked_children` (the one rank seam); this
    function adds the small-to-big parent expansion, the citation fields,
    and the parent-level rerank. ``hnsw_settings`` (U20) defaults to the
    ``search.hnsw.*`` config block; ``retrieval_settings`` (U45/U21)
    defaults to the ``search.*`` block. DuckDB runs the exact-scan twins
    in ``research/retrieval_duckdb.py`` (no pgvector, no SQL fusion; the
    pure rrf_fuse port fuses instead).
    """
    fused = await ranked_children(
        store, embedder, q, hnsw_settings=hnsw_settings, retrieval_settings=retrieval_settings
    )
    children = _children_from_fused(fused)
    duck = dialect_of(store).is_duckdb
    async with tenant_scope(store, q.tenant_id) as conn:
        windows = await _meeting_windows(conn, children, duck)
        parents = _to_parents(children, windows)
        citations = await _citations(conn, parents, windows, duck)

    if reranker is not None and q.rerank and citations:
        citations = await _rerank(q.query, citations, reranker)
    return citations[: q.k]


def _assignment_predicate(sql: _Sql, q: ResearchQuery) -> list[str]:
    """WHERE fragments for the assignment-grain filters (code/confidence/status)."""
    conditions: list[str] = []
    if q.code_id:
        conditions.append(f"ra.code_id = {sql.p(str(q.code_id))}")
    if q.min_confidence:
        levels = CONFIDENCE_FLOORS[q.min_confidence]
        placeholders = ", ".join(sql.p(level) for level in levels)
        conditions.append(f"ra.confidence IN ({placeholders})")
    if q.review_status:
        conditions.append(f"ra.status = {sql.p(str(q.review_status))}")
    return conditions


def _unit_filters(sql: _Sql, q: ResearchQuery, duck: bool = False) -> str:
    frag = ""
    if q.source_type:
        frag += f" AND d.source_type = {sql.p(q.source_type)}"
    if q.speaker_role:
        frag += f" AND s.role = {sql.p(q.speaker_role)}"
    if q.doc_ids:
        ids = sql.p([str(d) for d in q.doc_ids])
        # List membership: asyncpg's ANY on Postgres (byte-identical), the
        # engine's list_contains on DuckDB (no ``= ANY`` there).
        frag += (
            f" AND list_contains({ids}::VARCHAR[], u.doc_id)"
            if duck
            else f" AND u.doc_id = ANY({ids}::uuid[])"
        )
    if q.topic_id is not None:
        frag += (
            " AND u.exchange_id IN (SELECT exchange_id FROM research_exchanges "
            f"WHERE tenant_id = u.tenant_id AND topic_id = {sql.p(int(q.topic_id))})"
        )
    assignment = _assignment_predicate(sql, q)
    if assignment:
        frag += (
            " AND u.unit_id IN (SELECT ra.unit_id FROM research_assignments ra "
            f"WHERE ra.tenant_id = u.tenant_id AND {' AND '.join(assignment)})"
        )
    return frag


def _exchange_filters(sql: _Sql, q: ResearchQuery, duck: bool = False) -> str:
    frag = ""
    if q.source_type:
        frag += f" AND d.source_type = {sql.p(q.source_type)}"
    if q.speaker_role:
        member = (
            "list_contains(e.a_unit_ids, u2.unit_id)" if duck else "u2.unit_id = ANY(e.a_unit_ids)"
        )
        frag += (
            " AND EXISTS (SELECT 1 FROM research_units u2 "
            "JOIN research_speakers s2 ON s2.speaker_id = u2.speaker_id "
            f"WHERE {member} AND s2.role = {sql.p(q.speaker_role)})"
        )
    if q.doc_ids:
        ids = sql.p([str(d) for d in q.doc_ids])
        frag += (
            f" AND list_contains({ids}::VARCHAR[], e.doc_id)"
            if duck
            else f" AND e.doc_id = ANY({ids}::uuid[])"
        )
    if q.topic_id is not None:
        frag += f" AND e.topic_id = {sql.p(int(q.topic_id))}"
    assignment = _assignment_predicate(sql, q)
    if assignment:
        member = (
            "list_contains(e.a_unit_ids, ra.unit_id)" if duck else "ra.unit_id = ANY(e.a_unit_ids)"
        )
        frag += (
            " AND EXISTS (SELECT 1 FROM research_assignments ra "
            f"WHERE ra.tenant_id = e.tenant_id AND {member} "
            f"AND {' AND '.join(assignment)})"
        )
    return frag


async def _unit_arms(
    conn: SqlConn,
    query_vector: list[float] | None,
    q: ResearchQuery,
    depth: int,
    exact_threshold: float = DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
    duck: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if duck:
        from corpus_kb.research.retrieval_duckdb import unit_arms_duck

        return await unit_arms_duck(conn, query_vector, q, depth)
    dense_sql = _Sql(str(q.tenant_id), str(q.project_id))
    filters = _unit_filters(dense_sql, q)
    dense: list[dict[str, Any]] = []
    if query_vector is not None:
        vec = dense_sql.p(str(query_vector))
        limit = dense_sql.p(depth)
        # U21: with filters, probe matched/total once; a selective filter can
        # starve the ordered HNSW scan of candidates, so below the threshold
        # every matching row is scored exactly instead (same result schema).
        selectivity = (
            await estimate_selectivity(
                cast("SelectivityReader", conn),
                _UNIT_COUNT_SQL.format(filters=filters),
                list(dense_sql.values),
            )
            if filters
            else None
        )
        if should_use_exact_scan(bool(filters), selectivity, exact_threshold):
            # No distance ORDER BY/LIMIT inside the CTE: the planner cannot
            # use the ordered halfvec scan here, so the candidate set is the
            # COMPLETE filter match and the outer ordering is exact.
            rows = await conn.fetch(
                f"""
                WITH candidate AS MATERIALIZED (
                    SELECT u.unit_id, u.doc_id, u.exchange_id, u.seq, u.text, u.role_in_exchange,
                           1 - (u.embedding{_HALFVEC} <=> {vec}{_HALFVEC}) AS score
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
                """,
                *dense_sql.values,
            )
        else:
            # U40 relaxed-ordering pattern: the ANN scan (filters + distance
            # ORDER BY + LIMIT) stays INSIDE a MATERIALIZED CTE; the exact
            # ordering is applied OUTSIDE over the retrieved set (`score + 0`
            # defeats planner sort-elision, per the pgvector docs).
            rows = await conn.fetch(
                f"""
                WITH candidate AS MATERIALIZED (
                    SELECT u.unit_id, u.doc_id, u.exchange_id, u.seq, u.text, u.role_in_exchange,
                           1 - (u.embedding{_HALFVEC} <=> {vec}{_HALFVEC}) AS score
                    FROM research_units u
                    JOIN documents d ON d.doc_id = u.doc_id
                    LEFT JOIN research_speakers s ON s.speaker_id = u.speaker_id
                    WHERE u.tenant_id = $1 AND d.project_id = $2
                      AND u.embedding IS NOT NULL{filters}
                    ORDER BY u.embedding{_HALFVEC} <=> {vec}{_HALFVEC}
                    LIMIT {limit}
                )
                SELECT candidate.unit_id, candidate.doc_id, candidate.exchange_id,
                       candidate.seq, candidate.text, candidate.role_in_exchange,
                       candidate.score + 0 AS score
                FROM candidate
                ORDER BY candidate.score + 0 DESC, candidate.unit_id ASC
                """,
                *dense_sql.values,
            )
        dense = [_unit_payload(r) for r in rows]

    fts_sql = _Sql(str(q.tenant_id), str(q.project_id))
    fts_filters = _unit_filters(fts_sql, q)
    tsq = fts_sql.p(q.query)
    limit = fts_sql.p(depth)
    rows = await conn.fetch(
        f"""
        SELECT u.unit_id, u.doc_id, u.exchange_id, u.seq, u.text, u.role_in_exchange,
               ts_rank(to_tsvector('english', u.text), plainto_tsquery('english', {tsq})) AS score
        FROM research_units u
        JOIN documents d ON d.doc_id = u.doc_id
        LEFT JOIN research_speakers s ON s.speaker_id = u.speaker_id
        WHERE u.tenant_id = $1 AND d.project_id = $2{fts_filters}
          AND to_tsvector('english', u.text) @@ plainto_tsquery('english', {tsq})
        ORDER BY score DESC, u.unit_id ASC
        LIMIT {limit}
        """,
        *fts_sql.values,
    )
    return dense, [_unit_payload(r) for r in rows]


async def _exchange_arms(
    conn: SqlConn,
    query_vector: list[float] | None,
    q: ResearchQuery,
    depth: int,
    exact_threshold: float = DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
    duck: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if duck:
        from corpus_kb.research.retrieval_duckdb import exchange_arms_duck

        return await exchange_arms_duck(conn, query_vector, q, depth)
    dense_sql = _Sql(str(q.tenant_id), str(q.project_id))
    filters = _exchange_filters(dense_sql, q)
    dense: list[dict[str, Any]] = []
    if query_vector is not None:
        vec = dense_sql.p(str(query_vector))
        limit = dense_sql.p(depth)
        # U21: same probe + exact-scan fallback as _unit_arms.
        selectivity = (
            await estimate_selectivity(
                cast("SelectivityReader", conn),
                _EXCHANGE_COUNT_SQL.format(filters=filters),
                list(dense_sql.values),
            )
            if filters
            else None
        )
        if should_use_exact_scan(bool(filters), selectivity, exact_threshold):
            rows = await conn.fetch(
                f"""
                WITH candidate AS MATERIALIZED (
                    SELECT e.exchange_id, e.doc_id, e.seq, coalesce(e.qa_text, '') AS text,
                           1 - (e.embedding{_HALFVEC} <=> {vec}{_HALFVEC}) AS score
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
                """,
                *dense_sql.values,
            )
        else:
            # U40 relaxed-ordering pattern (see _unit_arms).
            rows = await conn.fetch(
                f"""
                WITH candidate AS MATERIALIZED (
                    SELECT e.exchange_id, e.doc_id, e.seq, coalesce(e.qa_text, '') AS text,
                           1 - (e.embedding{_HALFVEC} <=> {vec}{_HALFVEC}) AS score
                    FROM research_exchanges e
                    JOIN documents d ON d.doc_id = e.doc_id
                    WHERE e.tenant_id = $1 AND d.project_id = $2
                      AND e.embedding IS NOT NULL{filters}
                    ORDER BY e.embedding{_HALFVEC} <=> {vec}{_HALFVEC}
                    LIMIT {limit}
                )
                SELECT candidate.exchange_id, candidate.doc_id, candidate.seq,
                       candidate.text, candidate.score + 0 AS score
                FROM candidate
                ORDER BY candidate.score + 0 DESC, candidate.exchange_id ASC
                """,
                *dense_sql.values,
            )
        dense = [_exchange_payload(r) for r in rows]

    fts_sql = _Sql(str(q.tenant_id), str(q.project_id))
    fts_filters = _exchange_filters(fts_sql, q)
    tsq = fts_sql.p(q.query)
    limit = fts_sql.p(depth)
    rows = await conn.fetch(
        f"""
        SELECT e.exchange_id, e.doc_id, e.seq, coalesce(e.qa_text, '') AS text,
               ts_rank(to_tsvector('english', coalesce(e.qa_text, '')),
                       plainto_tsquery('english', {tsq})) AS score
        FROM research_exchanges e
        JOIN documents d ON d.doc_id = e.doc_id
        WHERE e.tenant_id = $1 AND d.project_id = $2 AND coalesce(e.qa_text, '') <> ''{fts_filters}
          AND to_tsvector('english', coalesce(e.qa_text, ''))
              @@ plainto_tsquery('english', {tsq})
        ORDER BY score DESC, e.exchange_id ASC
        LIMIT {limit}
        """,
        *fts_sql.values,
    )
    return dense, [_exchange_payload(r) for r in rows]


def _unit_payload(row: Any) -> dict[str, Any]:
    unit_id = int(row["unit_id"])
    exchange_id = row["exchange_id"]
    payload: dict[str, Any] = {
        "chunk_id": _child_uuid(f"unit:{unit_id}"),
        "text": str(row["text"]),
        "source": "",
        "doc_id": str(row["doc_id"]),
        "score": float(row["score"]),
        "_key": f"unit:{unit_id}",
        "_kind": unit_kind(row["role_in_exchange"]),
        "_unit_id": unit_id,
        "_exchange_id": None if exchange_id is None else int(exchange_id),
        "_seq": int(row["seq"]),
        "_doc_id": str(row["doc_id"]),
    }
    return payload


def _exchange_payload(row: Any) -> dict[str, Any]:
    exchange_id = int(row["exchange_id"])
    payload: dict[str, Any] = {
        "chunk_id": _child_uuid(f"exchange:{exchange_id}"),
        "text": str(row["text"]),
        "source": "",
        "doc_id": str(row["doc_id"]),
        "score": float(row["score"]),
        "_key": f"exchange:{exchange_id}",
        "_kind": CHILD_QA,
        "_unit_id": None,
        "_exchange_id": exchange_id,
        "_seq": int(row["seq"]),
        "_doc_id": str(row["doc_id"]),
    }
    return payload


async def _rrf(
    conn: SqlConn,
    dense: list[dict[str, Any]],
    lexical: list[dict[str, Any]],
    depth: int,
    rrf_k: int,
    duck: bool = False,
) -> list[dict[str, Any]]:
    if not dense and not lexical:
        return []
    if duck:
        from corpus_kb.research.retrieval_duckdb import rrf_pure

        return rrf_pure(dense, lexical, depth, rrf_k)
    try:
        rows = await conn.fetch(
            "SELECT chunk_id, text, source, doc_id, score "
            "FROM corpus.rrf_fusion($1::jsonb, $2::jsonb, $3, $4)",
            json.dumps(dense),
            json.dumps(lexical),
            depth,
            rrf_k,
        )
    except asyncpg.UndefinedFunctionError as exc:
        raise missing_rrf_fusion_error(exc) from exc
    by_uuid = {str(row["chunk_id"]): row for row in dense + lexical}
    fused: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "chunk_id": str(row["chunk_id"]),
            "text": row["text"],
            "source": "",
            "doc_id": str(row["doc_id"]),
            "score": float(row["score"]),
        }
        meta = by_uuid.get(str(row["chunk_id"]))
        if meta is not None:
            for name in ("_key", "_kind", "_unit_id", "_exchange_id", "_seq", "_doc_id"):
                if name in meta:
                    item[name] = meta[name]
        fused.append(item)
    # Zero-drift determinism (rule 9): fused score DESC, then chunk id ASC.
    # The SQL fusion returns this order (migration 024); the sort re-asserts
    # it so tied rows cannot permute between runs.
    fused.sort(key=lambda item: (-cast("float", item["score"]), str(item["chunk_id"])))
    return fused


def _children_from_fused(fused: list[dict[str, Any]]) -> list[_Child]:
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


async def _rerank(query: str, citations: list[Citation], reranker: object) -> list[Citation]:
    score = getattr(reranker, "score", None)
    if not callable(score):
        return citations
    texts = [
        f"{c.question_text}\n{c.answer_text}" if c.question_text else c.answer_text
        for c in citations
    ]
    raw = await asyncio.to_thread(score, query, texts)
    if not raw:
        return citations
    scores = [float(value) for value in cast("list[float]", raw)]
    lo, hi = (min(scores), max(scores)) if scores else (0.0, 1.0)
    span = (hi - lo) or 1.0
    ranked = sorted(zip(citations, scores, strict=True), key=lambda pair: pair[1], reverse=True)
    return [replace(c, score=(s - lo) / span) for c, s in ranked]


async def _rerank_children(
    query: str, rows: list[dict[str, Any]], reranker: object
) -> list[dict[str, Any]]:
    """Rerank fused child rows in place of the parent-level ``_rerank``.

    Same scorer contract and the same min-max calibration as ``_rerank``;
    ties break by chunk id so the order stays deterministic (rule 9).
    """
    score = getattr(reranker, "score", None)
    if not callable(score):
        return rows
    raw = await asyncio.to_thread(score, query, [str(row["text"]) for row in rows])
    if not raw:
        return rows
    scores = [float(value) for value in cast("list[float]", raw)]
    lo, hi = (min(scores), max(scores)) if scores else (0.0, 1.0)
    span = (hi - lo) or 1.0
    ranked = sorted(
        zip(rows, scores, strict=True),
        key=lambda pair: (-pair[1], str(pair[0]["chunk_id"])),
    )
    return [dict(row, score=(s - lo) / span) for row, s in ranked]
