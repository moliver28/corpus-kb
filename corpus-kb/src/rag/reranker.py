"""Rerankers for Corpus-KB.

IdentityReranker (``search.reranker: none``, the default): pass-through —
results are already fused by ``corpus.rrf_fusion()``.
PgmlReranker (``search.reranker: pgml``): cross-encoder reranking via
``pgml.rank()`` inside PostgreSQL.

On any pgml failure PgmlReranker logs a warning and returns the input
unchanged so callers can continue operating in degraded mode — reranking
must never break search.
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol, cast, runtime_checkable

import asyncpg

from ..config import load_config
from ..domain.models import SearchResult
from .embedder import _str_or_default

logger = logging.getLogger(__name__)

DEFAULT_RERANKER = "none"
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RANK_TIMEOUT_SECONDS = 30.0


@runtime_checkable
class Reranker(Protocol):
    """Protocol for search-result rerankers."""

    async def rerank(
        self, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        """Return ``results`` re-ranked by relevance to ``query``."""
        ...


class IdentityReranker:
    """Pass-through reranker — returns the fused results unchanged."""

    async def rerank(
        self, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        """Return ``results`` as-is; RRF fusion already ranked them."""
        return results


class PgmlReranker:
    """Cross-encoder reranker via ``pgml.rank()`` inside PostgreSQL.

    Sends the query and the fused result texts as bound parameters to
    ``pgml.rank(model, query, texts::text[])`` and reorders the results by
    the returned scores (descending, stable). The ``SearchResult.score``
    field keeps its RRF fusion value — only the order changes.

    On missing pool, pgml failure, timeout, or a score/result count
    mismatch, logs a warning and returns the input unchanged.
    """

    def __init__(
        self,
        config: Optional[dict[str, object]] = None,
        pool: Optional[asyncpg.Pool] = None,
    ) -> None:
        self._config = config or load_config()
        search = cast(dict[str, object], self._config.get("search", {}))
        self.model = _str_or_default(search, "reranker_model", DEFAULT_RERANKER_MODEL)
        self._pool = pool

    async def rerank(
        self, query: str, results: list[SearchResult]
    ) -> list[SearchResult]:
        """Reorder ``results`` by cross-encoder score via ``pgml.rank()``."""
        if not results:
            return results
        if self._pool is None:
            logger.warning("PgmlReranker has no pool; returning unreranked results.")
            return results

        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT score FROM pgml.rank($1, $2, $3::text[]) AS score",
                    self.model,
                    query,
                    [r.text for r in results],
                    timeout=RANK_TIMEOUT_SECONDS,
                )
        except Exception as exc:  # degraded-mode contract: never break search
            logger.warning("pgml.rank() failed: %s; returning unreranked results.", exc)
            return results

        scores = [float(row["score"]) for row in rows]
        if len(scores) != len(results):
            logger.warning(
                "pgml.rank() returned %d scores for %d results; "
                "returning unreranked results.",
                len(scores),
                len(results),
            )
            return results

        order = sorted(range(len(results)), key=lambda i: scores[i], reverse=True)
        return [results[i] for i in order]


def create_reranker(
    config: Optional[dict[str, object]] = None,
    pool: Optional[asyncpg.Pool] = None,
) -> Reranker:
    """Return the reranker selected by ``search.reranker`` in config.

    - ``"none"`` (default): IdentityReranker — pass-through.
    - ``"pgml"``: PgmlReranker — cross-encoder reranking via the pool.

    Raises ``ValueError`` for any other value.
    """
    cfg = config or load_config()
    search = cast(dict[str, object], cfg.get("search", {}))
    reranker = _str_or_default(search, "reranker", DEFAULT_RERANKER)
    if reranker == "none":
        return IdentityReranker()
    if reranker == "pgml":
        return PgmlReranker(cfg, pool)
    raise ValueError(f"Unknown search.reranker {reranker!r}: expected 'none' or 'pgml'")
