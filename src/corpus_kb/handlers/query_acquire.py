"""The arm-acquisition mechanics of the search pipeline (250-line split).

Extracted from ``query_pipeline.search_with_meta`` when that file hit
the 250-line soft limit: the per-run arm values (:class:`ArmRun`), the
engine connection stage (:func:`arm_outcome`), and the result assembly
(:func:`post_fusion`, :func:`final_envelope`). ``query_pipeline`` keeps
the resolution helpers and the public ``search_with_meta`` entry. The
behavior and the call order are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import asyncpg

from corpus_kb.domain.models import SearchQuery, SearchResult
from corpus_kb.handlers import query_stages
from corpus_kb.handlers.query_integrity import SearchEnvelope, finalize_envelope
from corpus_kb.rag.rerank_settings import RerankSettings
from corpus_kb.research.retrieval_settings import RetrievalSettings
from corpus_kb.research.search_settings import (
    HnswSettings,
    SqlExecutor,
    VersionReader,
    apply_hnsw_settings,
    pgvector_version,
    supports_iterative_scan,
)
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.gateway import SqlGateway
from corpus_kb.storage.search_backend import SearchBackend, SearchOutcome, SearchRequest


@dataclass(frozen=True)
class ArmRun:
    """The per-run values shared by arm acquisition and post-fusion.

    ``dynamic_predicates`` stays the bare list ``resolve_predicates``
    returns (the module's existing seam); ``reasons`` is the shared
    impairment list the arms append to and the envelope reads.
    """

    query: SearchQuery
    semantic_query: str
    dynamic_predicates: list
    degraded_pred: str
    over_retrieve: int
    reasons: list[str]


async def arm_outcome(
    pool: asyncpg.Pool | SqlGateway,
    search_backend: SearchBackend,
    run: ArmRun,
    hnsw_settings: HnswSettings,
) -> SearchOutcome:
    """Run both arms behind one connection; return the backend's outcome.

    Storage-portability §13 (sp-duckdb-search): on DuckDB one gateway
    transaction wraps both arms — no tenant GUC (no RLS; explicit
    ``tenant_id`` predicates) and no pgvector/HNSW preamble (DuckDB
    exact-scans the column). On Postgres the shipped asyncpg pool is
    used (test fakes included) with the tenant GUC preamble.
    """

    def request(conn: asyncpg.Connection) -> SearchRequest:
        return SearchRequest(
            conn=conn,
            query=run.query,
            semantic_query=run.semantic_query,
            dynamic_predicates=run.dynamic_predicates,
            degraded_pred=run.degraded_pred,
            over_retrieve_n=run.over_retrieve,
            reasons=run.reasons,
        )

    if isinstance(pool, DuckDbGateway):
        gateway = cast("DuckDbGateway", pool)
        async with gateway.transaction(str(run.query.tenant_id)):
            return await search_backend.search(request(cast("asyncpg.Connection", gateway)))
    pg_pool = cast("asyncpg.Pool", pool)
    async with pg_pool.acquire() as acquired, acquired.transaction():
        conn = cast("asyncpg.Connection", acquired)
        await conn.execute(
            "SELECT set_config('app.current_tenant_id', $1, true)",
            str(run.query.tenant_id),
        )
        # U20: transaction-local HNSW settings (SET LOCAL dies with this
        # transaction — a pooled connection never leaks them). Gated on the
        # installed pgvector version: emitting `SET LOCAL hnsw.*` below 0.8
        # aborts the query, so unknown/old versions fail closed.
        extversion = await pgvector_version(cast("VersionReader", conn))
        if supports_iterative_scan(extversion):
            await apply_hnsw_settings(cast("SqlExecutor", conn), hnsw_settings)
        return await search_backend.search(request(conn))


async def post_fusion(
    reranker: object | None,
    rerank_settings: RerankSettings,
    pool: asyncpg.Pool | SqlGateway,
    retrieval_settings: RetrievalSettings,
    outcome: SearchOutcome,
    run: ArmRun,
) -> list[SearchResult]:
    """RRF fusion (the determinism pin region), rerank, parent expansion.

    The fusion is the exact port of corpus.rrf_fusion (migration 024);
    no fusion SQL is issued on this path (research/retrieval.py keeps
    the SQL function). The rerank stage runs outside the handler's
    connection so a max_size=1 pool cannot deadlock on the reranker's
    own acquire.
    """
    k = run.query.k if run.query.k is not None else rerank_settings.return_top_k
    fused_results = query_stages.fuse_arm_rows(outcome, k, retrieval_settings.rrf_k)
    resolved_k = run.query.k if run.query.k is not None else rerank_settings.return_top_k
    return await query_stages.post_fuse(
        reranker,
        rerank_settings,
        pool,
        retrieval_settings,
        fused_results,
        run.semantic_query,
        resolved_k,
        run.over_retrieve,
        str(run.query.tenant_id),
        run.reasons,
    )


def final_envelope(
    outcome: SearchOutcome, results: list[SearchResult], run: ArmRun
) -> SearchEnvelope:
    """Attach the degraded-honesty accounting to the final results."""
    return finalize_envelope(
        results,
        run.reasons,
        excluded_count=outcome.excluded_count,
        vector_index_mode=outcome.vector_index_mode,
        lexical_index_mode=outcome.lexical_index_mode,
        vector_index_note=outcome.vector_index_note,
        lexical_index_note=outcome.lexical_index_note,
        meta_hits=[dict(row) for row in outcome.meta_rows],
    )
