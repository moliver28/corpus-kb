"""DuckDB SearchBackend: the search arms over the mirror schema (§13 sp-duckdb-search).

Serves ``query``, ``ask`` and the ``/v1`` adapter through the EXISTING
``SearchBackend`` protocol — QueryHandler keeps the orchestration and the
engine-neutral RRF fusion (handlers/query_fusion.py); this module carries
the engine split of the arms. Storage owns it, so the module imports
downward only (domain, rag, storage) — never handlers.

Engine shape (W0-verified, findings/search-fts.md):

- VECTOR ARM: exact cosine scan (DuckDB ships no HNSW in this regime,
  migrations_duckdb/025). Zero-vector candidate rows get an explicit
  sentinel flag and sort deterministically LAST — ``array_cosine_distance``
  returns a finite 2.0 for them (not NaN like pgvector). Postgres keeps
  such rows unreachable via the degraded filter, so the sentinel never
  changes a PG-reachable order.
- LEXICAL ARM: the 1.4 scalar ``match_bm25`` macro (no table-function
  form), param-bound — never interpolated — over the sanitized query.
  Disjunctive only: conjunctive plus stopword zeroes out (duckdb-fts #15,
  present in 1.4.5). A query with no usable terms skips the arm; the
  count-watermark FTS rebuild runs first (storage/duckdb_fts.py).

Reason strings and the arm SELECT list mirror handlers.query_integrity /
handlers.query_vector_arms, which storage must not import; the parity is
pinned literally by tests/test_duckdb_search.py.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable
from typing import Any, Protocol, cast

from corpus_kb.config import load_config
from corpus_kb.rag.rerank_settings import load_rerank_settings
from corpus_kb.rag.self_query import build_filter_sql
from corpus_kb.research.retrieval_settings import load_retrieval_settings
from corpus_kb.storage.dialect import DUCKDB, sanitize_fts_query
from corpus_kb.storage.duckdb_fts import ensure_fts_index
from corpus_kb.storage.search_backend import SearchOutcome, SearchRequest

logger = logging.getLogger(__name__)

#: Engine-honesty modes (program §6): DuckDB scans the vector column
#: exactly and serves lexical search from the lazily rebuilt fts index.
DUCKDB_VECTOR_INDEX_MODE = "exact_scan"
DUCKDB_LEXICAL_INDEX_MODE = "fts_bm25"

# Mirrors of handlers.query_integrity reason strings (pinned by tests).
REASON_ZERO_QUERY = "zero-query-embedding"
REASON_VECTOR_ARM_ERROR = "vector-arm-error"
REASON_NO_EMBEDDER = "no-embedder"
REASON_FILTER_EMPTY = "degraded-filter-active"

# Mirrors of handlers.query_vector_arms fragments (pinned by tests).
SELECT_LIST = (
    "chunks.chunk_id, chunks.text, chunks.doc_id, documents.source, chunks.file_path,"
    " chunks.start_line, chunks.end_line, chunks.chunk_index, chunks.heading_path"
)
DEDUP_PREDICATE = "AND chunks.tombstoned_at IS NULL AND chunks.superseded_at IS NULL"

_DISTANCE = DUCKDB.vector_distance("cv.embedding", "$1")
_ZERO_FLAG = f"CASE WHEN NOT ({DUCKDB.zero_vector_guard('cv.embedding')}) THEN 1 ELSE 0 END"
_BM25 = DUCKDB.fts_rank("chunks.chunk_id", "$1")

_VECTOR_ARM_SQL = f"""
WITH candidate AS MATERIALIZED (
    SELECT cv.chunk_id AS candidate_chunk_id,
           {_DISTANCE} AS dist,
           {_ZERO_FLAG} AS zero_flag
    FROM chunks_vectors cv
    JOIN chunks ON cv.chunk_id = chunks.chunk_id
    JOIN documents ON chunks.doc_id = documents.doc_id
    WHERE cv.tenant_id = $2 {{dedup}} {{frag}} {{degraded_pred}}
)
SELECT {SELECT_LIST}, (1 - candidate.dist) AS score
FROM candidate
JOIN chunks ON candidate.candidate_chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
ORDER BY candidate.zero_flag ASC, (1 - candidate.dist) DESC, chunks.chunk_id ASC
LIMIT $3
"""

_LEXICAL_ARM_SQL = f"""
SELECT {SELECT_LIST}, scored.score
FROM (
    SELECT chunks.chunk_id, {_BM25} AS score
    FROM chunks
) AS scored
JOIN chunks ON scored.chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
WHERE scored.score IS NOT NULL
  AND chunks.tenant_id = $2 {{dedup}} {{frag}}
ORDER BY scored.score DESC, chunks.chunk_id ASC
LIMIT $3
"""

_DEGRADED_PROBE_SQL = f"""
SELECT count(*)::float8 AS candidates
FROM chunks_vectors cv
JOIN chunks ON cv.chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
WHERE cv.tenant_id = $1 {DEDUP_PREDICATE} {{frag}}
"""


class QueryEmbedder(Protocol):
    """The one embedder call the dense arm needs.

    ``embed`` may be sync (OllamaEmbedder, test stubs) or async
    (PgmlEmbedder); the arm awaits an awaitable result, so both satisfy it.
    """

    def embed(self, text: str) -> list[float] | Awaitable[list[float]]: ...


def _all_zero(vector: list[float] | None) -> bool:
    """True when the query embedding is the degraded all-zero marker."""
    return vector is not None and all(v == 0.0 for v in vector)


def vector_literal(vector: list[float]) -> str:
    """Pack a query vector as a no-space ``[v1,v2,...]`` varchar literal.

    DuckDB 1.4.5 converts a bound fixed-size ARRAY parameter (from a list
    OR numpy array) in roughly 2.7 s per 2560-float statement; the varchar
    CAST of the same values (``$1::FLOAT[k]``, spike-5 verified, no spaces
    after commas) costs about 10 ms. ``.9g`` is float32-faithful and
    deterministic, so equal queries bind equal strings and replay
    identical scores.
    """
    return "[" + ",".join(format(float(v), ".9g") for v in vector) + "]"


class DuckDbSearchBackend:
    """The DuckDB search arms on the SearchBackend protocol."""

    def __init__(
        self,
        embedder: QueryEmbedder | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._embedder = embedder
        self._config = config if config is not None else load_config()
        self._rerank_settings = load_rerank_settings(self._config)
        self._retrieval_settings = load_retrieval_settings(self._config)
        self._exclude_degraded = self._retrieval_settings.exclude_degraded

    async def search(self, request: SearchRequest) -> SearchOutcome:
        """Arms + degraded probe on ONE gateway; fusion stays in the handler.

        ``request.conn`` is the DuckDbGateway inside the handler's
        transaction scope (tenancy rides the explicit ``tenant_id``
        predicates — DuckDB has no RLS). Impairments go to
        ``request.reasons`` exactly like the PG backend.
        """
        gateway = cast("Any", request.conn)
        tenant_id = str(request.query.tenant_id)
        k = request.query.k if request.query.k is not None else self._rerank_settings.return_top_k

        vector_rows, vector_reason = await self._vector_rows(request, gateway, tenant_id, k)
        if vector_reason:
            request.reasons.append(vector_reason)
        excluded_count = await self._degraded_probe(gateway, request, tenant_id, vector_rows)
        lexical_rows = await self._lexical_rows(request, gateway, tenant_id, k)
        return SearchOutcome(
            vector_rows=vector_rows,
            lexical_rows=lexical_rows,
            excluded_count=excluded_count,
            vector_index_mode=DUCKDB_VECTOR_INDEX_MODE,
            lexical_index_mode=DUCKDB_LEXICAL_INDEX_MODE,
        )

    async def _vector_rows(
        self, request: SearchRequest, gateway: Any, tenant_id: str, k: int
    ) -> tuple[list[Any], str | None]:
        """Exact cosine scan; impairments degrade to the lexical arm alone."""
        try:
            if self._embedder is None:
                return [], REASON_NO_EMBEDDER
            embedded = self._embedder.embed(request.semantic_query)
            query_vector = await embedded if inspect.isawaitable(embedded) else embedded
            if _all_zero(query_vector):
                return [], REASON_ZERO_QUERY
            frag, params = build_filter_sql(request.dynamic_predicates, start_index=4)
            limit = max(self._retrieval_settings.vector_candidates, k * 2)
            rows = await gateway.fetch(
                _VECTOR_ARM_SQL.format(
                    dedup=DEDUP_PREDICATE,
                    frag=frag,
                    degraded_pred=request.degraded_pred,
                ),
                vector_literal(query_vector),
                tenant_id,
                limit,
                *params,
            )
            return list(rows), None
        except Exception as exc:
            logger.warning("DuckDB vector arm failed: %s", exc)
            return [], f"{REASON_VECTOR_ARM_ERROR} ({type(exc).__name__})"

    async def _lexical_rows(
        self, request: SearchRequest, gateway: Any, tenant_id: str, k: int
    ) -> list[Any]:
        """Sanitized, param-bound bm25 arm; a termless query skips it.

        No try/except (PG parity): a failed lexical query fails the search
        loudly instead of returning a silent FTS-only answer.
        """
        terms = sanitize_fts_query(request.semantic_query)
        if not terms:
            return []
        await ensure_fts_index(gateway)
        frag, params = build_filter_sql(request.dynamic_predicates, start_index=4)
        limit = max(self._retrieval_settings.lexical_candidates, k * 2)
        rows = await gateway.fetch(
            _LEXICAL_ARM_SQL.format(dedup=DEDUP_PREDICATE, frag=frag),
            terms,
            tenant_id,
            limit,
            *params,
        )
        return list(rows)

    async def _degraded_probe(
        self, gateway: Any, request: SearchRequest, tenant_id: str, vector_rows: list[Any]
    ) -> int:
        """Count rows the degraded filter excluded (0 when no probe ran).

        Fail-open (PG contract): a broken probe only costs the annotation.
        """
        if not (self._exclude_degraded and not vector_rows and not request.reasons):
            return 0
        frag, params = build_filter_sql(request.dynamic_predicates, start_index=2)
        try:
            probe_row = await gateway.fetchrow(
                _DEGRADED_PROBE_SQL.format(frag=frag), tenant_id, *params
            )
        except Exception as exc:
            logger.warning("DuckDB degraded-filter count probe failed: %s", exc)
            return 0
        if probe_row is None:
            return 0
        raw = probe_row["candidates"]
        excluded = int(raw) if isinstance(raw, (int, float)) else 0
        if excluded > 0:
            request.reasons.append(REASON_FILTER_EMPTY)
        return excluded
