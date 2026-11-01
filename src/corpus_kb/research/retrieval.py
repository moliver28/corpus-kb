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

The shared filter compiler and grain specs live in
``research/retrieval_filters.py``, the arm and payload builders in
``research/retrieval_arms.py``, and the reranker seam in
``research/retrieval_rerank.py``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import asyncpg

from corpus_kb.config import load_config
from corpus_kb.projections.research._embed import ResearchEmbedder
from corpus_kb.research.citations import Citation, _citations, _meeting_windows, _to_parents
from corpus_kb.research.conn import SqlConn, Store, dialect_of, tenant_scope
from corpus_kb.research.fts_duckdb import ensure_research_fts
from corpus_kb.research.retrieval_arms import (
    _child_uuid,
    _children_from_fused,
    _exchange_arms,
    _exchange_payload,
    _unit_arms,
    _unit_payload,
)
from corpus_kb.research.retrieval_filters import (
    CONFIDENCE_FLOORS,
    _exchange_filters,
    _Sql,
    _unit_filters,
)
from corpus_kb.research.retrieval_rerank import _rerank, _rerank_children
from corpus_kb.research.retrieval_settings import RetrievalSettings, load_retrieval_settings
from corpus_kb.research.search_settings import (
    PGVECTOR_VERSION_SQL,
    HnswSettings,
    SqlExecutor,
    apply_hnsw_settings,
    load_hnsw_settings,
    missing_rrf_fusion_error,
    supports_iterative_scan,
)

__all__ = [
    "CONFIDENCE_FLOORS",
    "ResearchQuery",
    "_Sql",
    "_child_uuid",
    "_children_from_fused",
    "_exchange_arms",
    "_exchange_filters",
    "_exchange_payload",
    "_unit_arms",
    "_unit_filters",
    "_unit_payload",
    "ranked_children",
    "research_search",
]

logger = logging.getLogger(__name__)

CANDIDATE_MULTIPLIER = 8
RERANK_MIN_CANDIDATES = 30


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
    """Search children, return parent citations (small-to-big).

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
