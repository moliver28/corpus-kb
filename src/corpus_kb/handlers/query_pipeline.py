"""The search pipeline's arm-acquisition stage (pure move, 250-line rule).

Functions moved out of ``query_handler.QueryHandler`` byte-identical: the
engine selection, the reranker resolution, the W3 default-k contract, the
self-query predicates, the candidate inflation, the DuckDB arm wrapper, and
``search_with_meta``. The class keeps thin delegates. The result stages
(fusion pin, rerank, parent expansion) live in :mod:`query_stages`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import asyncpg

from corpus_kb.domain.models import SearchQuery
from corpus_kb.handlers import query_stages
from corpus_kb.handlers.query_integrity import (
    SearchEnvelope,
    degraded_candidate_predicate,
    finalize_envelope,
)
from corpus_kb.handlers.query_search_pg import PostgresSearchBackend
from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder
from corpus_kb.rag.rerank_settings import RerankSettings
from corpus_kb.rag.reranker import build_reranker, create_reranker
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
from corpus_kb.storage.duckdb_search import DuckDbSearchBackend
from corpus_kb.storage.gateway import SqlGateway
from corpus_kb.storage.search_backend import SearchBackend, SearchRequest
from corpus_kb.utils.tracing import SPAN_QUERY, span


def select_search_backend(
    pool: asyncpg.Pool | SqlGateway,
    embedder: OllamaEmbedder | PgmlEmbedder | None,
    config: dict[str, object],
    search_backend: SearchBackend | None,
) -> SearchBackend:
    """Storage-portability §3.3: the engine split sits behind the
    SearchBackend protocol. No façade — the default is the engine impl
    for the pool kind (§13: DuckDbGateway -> the DuckDB arms; asyncpg
    pools -> the PG arms), from this handler's config and embedder.
    """
    if search_backend is not None:
        return search_backend
    if isinstance(pool, DuckDbGateway):
        return DuckDbSearchBackend(embedder=embedder, config=config)
    return PostgresSearchBackend(embedder=embedder, config=config)


def resolve_reranker(config: dict[str, object], pool: asyncpg.Pool | SqlGateway) -> object | None:
    """U30 production wiring: ``search.rerank.enabled`` takes precedence;
    when absent/disabled, fall back to the legacy ``search.reranker``
    selection. Only the DuckDB gateway pool skips the pool-driven factory
    (§13: a gateway builds no pool-backed reranker).
    """
    built = build_reranker(config)
    if built is not None or isinstance(pool, DuckDbGateway):
        return built
    return create_reranker(config, cast("asyncpg.Pool", pool))


def resolve_default_k(query: SearchQuery, rerank_settings: RerankSettings) -> SearchQuery:
    """W3 default-k contract: ``k=None`` (caller omitted k) becomes
    ``reranking.return_top_k``; an explicit caller k always wins."""
    if query.k is None:
        return query.model_copy(update={"k": rerank_settings.return_top_k})
    return query


def resolve_predicates(
    query: SearchQuery, self_query_parser: object | None, self_query_enabled: bool
) -> tuple[str, list]:
    """Resolve the semantic query and its dynamic filter predicates.

    Self-query parsing (when enabled) rewrites the semantic query and
    adds parsed predicates; an explicit ``source_type`` filter adds one.
    """
    semantic_query = query.query
    dynamic_predicates: list = []
    use_self_query = query.self_query if query.self_query is not None else self_query_enabled
    if use_self_query and self_query_parser is not None:
        # Duck-typed test seam (stubs pass _StubParser); the cast keeps
        # that contract without widening the stored attribute's type.
        from corpus_kb.rag.self_query import ParsedQuery

        parse_fn = cast("Callable[[str], ParsedQuery]", getattr(self_query_parser, "parse", None))
        parsed = parse_fn(query.query)
        semantic_query = parsed.semantic_query
        dynamic_predicates = list(parsed.predicates)
    if query.source_type:
        from corpus_kb.rag.self_query import Predicate

        dynamic_predicates.append(
            Predicate(
                target="documents.source_type",
                op="=",
                value=query.source_type,
            )
        )
    return semantic_query, dynamic_predicates


def over_retrieve_n(reranker: object | None, rerank_settings: RerankSettings) -> int:
    """Candidate LIMIT inflation for score-based pipeline rerankers.

    Computed up front so the FTS arm and the post-connection reranker can
    always reference it (scoping it inside `if embedder:` once raised
    UnboundLocalError for embedder-less handlers). Only score-based
    pipeline rerankers (the `search.rerank` system) over-retrieve; the
    async `.rerank` rerankers fuse to top-k directly.
    """
    return rerank_settings.over_retrieve_n if callable(getattr(reranker, "score", None)) else 0


async def search_with_meta(
    pool: asyncpg.Pool | SqlGateway,
    search_backend: SearchBackend,
    reranker: object | None,
    rerank_settings: RerankSettings,
    retrieval_settings: RetrievalSettings,
    hnsw_settings: HnswSettings,
    exclude_degraded: bool,
    self_query_parser: object | None,
    self_query_enabled: bool,
    query: SearchQuery,
) -> SearchEnvelope:
    """Hybrid search with the degraded-honesty envelope attached.

    Byte-identical to the pre-split ``QueryHandler`` method: the envelope
    reports ``degraded_excluded`` and flips to ``degraded_empty`` (+ pinned
    remedy) when nothing was returned under degraded conditions.
    """
    with span(SPAN_QUERY):
        query = resolve_default_k(query, rerank_settings)
        # Stage 3 honesty: every vector-arm impairment is recorded in
        # ``reasons`` and surfaced in the envelope's ``degraded_excluded`` —
        # the silent FTS-only fallbacks become visible.
        reasons: list[str] = []
        semantic_query, dynamic_predicates = resolve_predicates(
            query, self_query_parser, self_query_enabled
        )
        over_retrieve = over_retrieve_n(reranker, rerank_settings)

        degraded_pred = degraded_candidate_predicate(exclude_degraded)
        if isinstance(pool, DuckDbGateway):
            # Storage-portability §13 (sp-duckdb-search): the arms on
            # DuckDB. One gateway transaction wraps both arms. No tenant
            # GUC (no RLS — explicit ``tenant_id`` predicates) and no
            # pgvector/HNSW preamble (DuckDB exact-scans the column).
            gateway = cast("DuckDbGateway", pool)
            async with gateway.transaction(str(query.tenant_id)):
                outcome = await search_backend.search(
                    SearchRequest(
                        conn=cast("asyncpg.Connection", gateway),
                        query=query,
                        semantic_query=semantic_query,
                        dynamic_predicates=dynamic_predicates,
                        degraded_pred=degraded_pred,
                        over_retrieve_n=over_retrieve,
                        reasons=reasons,
                    )
                )
        else:
            # The gateway kind is decided above; what remains is the
            # shipped asyncpg pool (test fakes included).
            pg_pool = cast("asyncpg.Pool", pool)
            async with pg_pool.acquire() as acquired, acquired.transaction():
                conn = cast("asyncpg.Connection", acquired)
                await conn.execute(
                    "SELECT set_config('app.current_tenant_id', $1, true)",
                    str(query.tenant_id),
                )
                # U20: transaction-local HNSW settings (SET LOCAL dies with this
                # transaction — a pooled connection never leaks them). Gated on
                # the installed pgvector version: emitting `SET LOCAL hnsw.*`
                # below 0.8 aborts the query, so unknown/old versions keep the
                # pre-iterative-scan behavior (fail closed).
                extversion = await pgvector_version(cast("VersionReader", conn))
                if supports_iterative_scan(extversion):
                    await apply_hnsw_settings(cast("SqlExecutor", conn), hnsw_settings)

                outcome = await search_backend.search(
                    SearchRequest(
                        conn=conn,
                        query=query,
                        semantic_query=semantic_query,
                        dynamic_predicates=dynamic_predicates,
                        degraded_pred=degraded_pred,
                        over_retrieve_n=over_retrieve,
                        reasons=reasons,
                    )
                )

        # 3. RRF fusion in Python (handlers/query_fusion.py) — the exact
        #    port of corpus.rrf_fusion (migration 024); the determinism pin
        #    region lives in query_stages now. No fusion SQL is issued on
        #    this path; research/retrieval.py keeps the SQL function.
        k = query.k if query.k is not None else rerank_settings.return_top_k
        fused_results = query_stages.fuse_arm_rows(outcome, k, retrieval_settings.rrf_k)

        # 4. Reranking (post-RRF) + envelope finalization. The rerank stage
        #    runs outside the handler's connection so a max_size=1 pool
        #    cannot deadlock on the reranker's own acquire.
        resolved_k = query.k if query.k is not None else rerank_settings.return_top_k
        final_results = await query_stages.post_fuse(
            reranker,
            rerank_settings,
            pool,
            retrieval_settings,
            fused_results,
            semantic_query,
            resolved_k,
            over_retrieve,
            str(query.tenant_id),
            reasons,
        )
        return finalize_envelope(
            final_results,
            reasons,
            excluded_count=outcome.excluded_count,
            vector_index_mode=outcome.vector_index_mode,
            lexical_index_mode=outcome.lexical_index_mode,
            vector_index_note=outcome.vector_index_note,
            lexical_index_note=outcome.lexical_index_note,
            meta_hits=[dict(row) for row in outcome.meta_rows],
        )
