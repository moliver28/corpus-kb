"""Query handler — read-side queries via asyncpg + pgvector.

All queries use parameterized SQL with SET LOCAL app.current_tenant_id
for RLS enforcement. No SQLAlchemy — asyncpg only.

Zero-drift splits: Wave 3 moved the provider vector arms to
``handlers/query_vector_arms.py``, the small CRUD-ish handlers to
``handlers/query_crud.py``, and the PG search arms to
``handlers/query_search_pg.py`` behind the ``SearchBackend`` protocol
(storage-portability §3.3). The 250-line split (sp-suite-hardening) moved
the pipeline mechanics to ``handlers/query_pipeline.py`` (engine/preamble
stage) and the result stages to ``handlers/query_stages.py`` (the fusion
pin region, post-fuse rerank, parent expansion). This module keeps the
``QueryHandler`` orchestrator's constructor and public surface, the CRUD
delegates, and the singleton.

Usage:
    from corpus_kb.handlers.query_handler import get_query_handler
    handler = get_query_handler()
    results = await handler.handle_search(SearchQuery(query="test", k=10))
"""

from __future__ import annotations

from typing import Any, cast

import asyncpg

from corpus_kb.config import load_config
from corpus_kb.domain.models import (
    DocumentResult,
    EntityResult,
    ListDocumentsQuery,
    ListEntitiesQuery,
    SearchContextQuery,
    SearchQuery,
    SearchResult,
    SearchSimilarQuery,
    SQLQuery,
    VerifyAnswerQuery,
    VerifyAnswerResult,
)
from corpus_kb.errors import DependencyMissingError
from corpus_kb.handlers import query_crud, query_crud_reads, query_pipeline, query_stages
from corpus_kb.handlers.query_integrity import SearchEnvelope
from corpus_kb.rag.embedder import OllamaEmbedder, PgmlEmbedder
from corpus_kb.rag.rerank_settings import load_rerank_settings
from corpus_kb.research.retrieval_settings import load_retrieval_settings
from corpus_kb.research.search_settings import HnswSettings, load_hnsw_settings
from corpus_kb.storage.gateway import SqlGateway
from corpus_kb.storage.search_backend import SearchBackend


class QueryHandler:
    """Read-side query handler using asyncpg + pgvector.

    All methods are async and acquire a connection from the pool,
    set the tenant context via SET LOCAL, then execute parameterized SQL.
    """

    def __init__(
        self,
        pool: asyncpg.Pool | SqlGateway,
        embedder: OllamaEmbedder | PgmlEmbedder | None = None,
        reranker: object | None = None,
        self_query_parser: object | None = None,
        judge: object | None = None,
        config: dict[str, Any] | None = None,
        search_backend: SearchBackend | None = None,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._self_query_parser = self_query_parser
        self._judge = judge
        self._config = config or load_config()
        # Storage-portability §3.3: the engine split sits behind the
        # SearchBackend protocol (rationale in query_pipeline).
        self._search_backend: SearchBackend = query_pipeline.select_search_backend(
            pool, embedder, self._config, search_backend
        )
        # U30 production wiring: ``search.rerank.enabled`` takes precedence
        # over the legacy ``search.reranker`` selection (rationale in
        # query_pipeline.resolve_reranker).
        self._reranker = (
            reranker
            if reranker is not None
            else (query_pipeline.resolve_reranker(self._config, pool))
        )
        # Config v2: the retrieval knobs live under ``retrieval.*`` and the
        # rerank knobs under ``reranking.*`` (legacy namespaces are aliases
        # the loader already normalized). U45/U21: ``rrf_k`` feeds the
        # fusion call; the threshold drives the exact-scan arm; the
        # candidate depths drive the live vector/FTS arm LIMITs; ``mode``
        # is enum-validated here — a bad value fails loudly at construction.
        self._rerank_settings = load_rerank_settings(self._config)
        self._retrieval_settings = load_retrieval_settings(self._config)
        self._exclude_degraded = self._retrieval_settings.exclude_degraded
        self._self_query_enabled = bool(self._retrieval_settings.self_query.get("enabled", False))
        # U20: transaction-local HNSW scan settings (retrieval.hnsw.*).
        # U48/P6: the ingest-side hierarchy keys are owned by the pipeline;
        # the query-time expand_to_parent arm lives on retrieval settings.
        self._hnsw_settings: HnswSettings = load_hnsw_settings(self._config)

    async def handle_search(self, query: SearchQuery) -> list[SearchResult]:
        """Hybrid search: self-query filtering -> vector (matryoshka two-tier or
        pgml exact) + contextual FTS -> RRF fusion -> post-RRF reranking.
        """
        return (await self.handle_search_with_meta(query)).results

    async def handle_search_with_meta(self, query: SearchQuery) -> SearchEnvelope:
        """Hybrid search with the degraded-honesty envelope attached.

        Delegates to :func:`query_pipeline.search_with_meta` — the same
        call POST /api/search and the socket method make. Default k
        (W3 contract): ``k=None`` resolves to ``reranking.return_top_k``.
        """
        return await query_pipeline.search_with_meta(
            pool=self._pool,
            search_backend=self._search_backend,
            reranker=self._reranker,
            rerank_settings=self._rerank_settings,
            retrieval_settings=self._retrieval_settings,
            hnsw_settings=self._hnsw_settings,
            exclude_degraded=self._exclude_degraded,
            self_query_parser=self._self_query_parser,
            self_query_enabled=self._self_query_enabled,
            query=query,
        )

    def _resolve_default_k(self, query: SearchQuery) -> SearchQuery:
        """W3 default-k contract (delegate; body in query_pipeline)."""
        return query_pipeline.resolve_default_k(query, self._rerank_settings)

    async def _post_fuse(
        self,
        fused_results: list[SearchResult],
        semantic_query: str,
        k: int,
        over_retrieve_n: int,
        tenant_id: str,
        reasons: list[str] | None = None,
    ) -> list[SearchResult]:
        """Post-RRF rerank + parent expansion (delegate; body in query_stages)."""
        return await query_stages.post_fuse(
            self._reranker,
            self._rerank_settings,
            self._pool,
            self._retrieval_settings,
            fused_results,
            semantic_query,
            k,
            over_retrieve_n,
            tenant_id,
            reasons,
        )

    async def _maybe_expand_to_parent(
        self,
        results: list[SearchResult],
        tenant_id: str,
    ) -> list[SearchResult]:
        """U48/P6 parent expansion (delegate; body in query_stages)."""
        return await query_stages.maybe_expand_to_parent(
            self._pool, self._retrieval_settings, results, tenant_id
        )

    # -- Small CRUD-ish handlers (moved to handlers/query_crud.py and
    #    query_crud_reads.py; the delegating methods keep the public
    #    surface byte-identical). The widened union is type-truth: the
    #    modules dispatch the engine at runtime (is_gateway), so no cast
    #    may hide the gateway arm.

    async def handle_sql_query(self, query: SQLQuery) -> list[dict[str, Any]]:
        """Execute a read-only SQL query."""
        return await query_crud_reads.sql_query(
            cast("asyncpg.Pool | SqlGateway", self._pool), query
        )

    async def handle_list_documents(self, query: ListDocumentsQuery) -> list[DocumentResult]:
        """List documents with pagination."""
        return await query_crud.list_documents(cast("asyncpg.Pool | SqlGateway", self._pool), query)

    async def handle_list_entities(self, query: ListEntitiesQuery) -> list[EntityResult]:
        """List entities, optionally filtered by type."""
        return await query_crud_reads.list_entities(
            cast("asyncpg.Pool | SqlGateway", self._pool), query
        )

    async def handle_search_similar(self, query: SearchSimilarQuery) -> list[SearchResult]:
        """Find chunks similar to a given chunk via vector distance."""
        return await query_crud.search_similar(
            cast("asyncpg.Pool | SqlGateway", self._pool),
            exclude_degraded=self._exclude_degraded,
            query=query,
        )

    async def handle_search_context(self, query: SearchContextQuery) -> list[SearchResult]:
        """Search with surrounding context chunks."""
        return await query_crud.search_context(
            cast("asyncpg.Pool | SqlGateway", self._pool), self.handle_search, query
        )

    async def handle_verify_answer(self, query: VerifyAnswerQuery) -> VerifyAnswerResult:
        """Verify an LLM-generated answer's claims against cited chunks."""
        return await query_crud_reads.verify_answer(
            cast("asyncpg.Pool | SqlGateway", self._pool), judge=self._judge, query=query
        )


# ============================================================================
# Singleton
# ============================================================================

_query_handler: QueryHandler | None = None


def get_query_handler(pool: asyncpg.Pool | SqlGateway | None = None) -> QueryHandler:
    """Get or create the singleton QueryHandler."""
    global _query_handler
    if _query_handler is None:
        if pool is None:
            # Typed refusal (zero-drift rule 4): server_wiring_duckdb leaves
            # the handler unset, so the query routes fail with the taxonomy
            # class, never a bare RuntimeError -> 500.
            raise DependencyMissingError(
                "The query handler is not ported to the DuckDB backend.",
                remediation="Run the server on the Postgres backend for query routes.",
            )
        _query_handler = QueryHandler(pool)
    return _query_handler


def set_query_handler(handler: QueryHandler) -> None:
    """Set the singleton (for server wiring)."""
    global _query_handler
    _query_handler = handler
