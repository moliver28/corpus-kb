"""Rerankers for Corpus-KB.

IdentityReranker (``search.reranker: none``, the default): pass-through —
results are already fused by ``corpus.rrf_fusion()``.
PgmlReranker (``search.reranker: pgml``): cross-encoder reranking via
``pgml.rank()`` inside PostgreSQL.
OllamaReranker (``search.reranker: ollama``): scores (query, chunk) pairs
via a local Ollama generate call using the qwen3-reranker prompt format,
confirmed as ``"Query: {query}\nDocument: {doc}\nRelevance:"`` against
``ollama.generate()``.
FakeReranker: deterministic reranker for CI / degraded mode, mirroring
FakeEmbedder in src/rag/embedder.py.

On any pgml failure PgmlReranker logs a warning and returns the input
unchanged so callers can continue operating in degraded mode — reranking
must never break search. On any failure OllamaReranker returns None (not
raised, not zeros) so callers can fall open to the pre-rerank RRF order --
reranking is a strict quality add-on, never a hard dependency of search.
"""

from __future__ import annotations

import hashlib
import logging
import random
from collections import OrderedDict
from typing import Optional, Protocol, cast, runtime_checkable

import asyncpg
import httpx
from ollama import Client

from ..config import load_config
from ..domain.models import SearchResult
from .embedder import _str_or_default

logger = logging.getLogger(__name__)

DEFAULT_RERANKER = "none"
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RANK_TIMEOUT_SECONDS = 30.0
MAX_CACHE_SIZE = 10_000


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
    the returned scores (descending, stable), mapping each score back to
    its input document via ``corpus_id`` — pgml.rank emits rows in rank
    order, not input order. The ``SearchResult.score`` field keeps its
    RRF fusion value — only the order changes.

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
                    "SELECT corpus_id, score FROM pgml.rank($1, $2, $3::text[])",
                    self.model,
                    query,
                    [r.text for r in results],
                    timeout=RANK_TIMEOUT_SECONDS,
                )
        except Exception as exc:  # degraded-mode contract: never break search
            logger.warning("pgml.rank() failed: %s; returning unreranked results.", exc)
            return results

        # pgml.rank emits one row per document in RANK order (best first),
        # keyed by corpus_id = the document's index in the input array.
        # Map scores back by corpus_id — never by row position.
        scores: list[Optional[float]] = [None] * len(results)
        for row in rows:
            idx = int(row["corpus_id"])
            if 0 <= idx < len(results):
                scores[idx] = float(row["score"])
        matched = sum(score is not None for score in scores)
        if matched != len(results):
            logger.warning(
                "pgml.rank() returned scores for %d of %d results; "
                "returning unreranked results.",
                matched,
                len(results),
            )
            return results

        order = sorted(
            range(len(results)), key=lambda i: cast(float, scores[i]), reverse=True
        )
        return [results[i] for i in order]


class OllamaReranker:
    """Cross-encoder reranker using qwen3-reranker via Ollama's generate API."""

    def __init__(self, config: Optional[dict[str, object]] = None) -> None:
        rerank_cfg = cast(
            dict[str, object],
            ((config or {}).get("search", {}) or {}).get("rerank", {}) or {},
        )
        self.model = str(rerank_cfg.get("model", "qwen3-reranker:8b"))
        self.base_url = str(rerank_cfg.get("base_url", "http://localhost:11434"))
        self.batch_size = int(rerank_cfg.get("batch_size", 16))
        self._client = Client(host=self.base_url)
        self._cache: OrderedDict[str, float] = OrderedDict()

    def score(self, query: str, texts: list[str]) -> Optional[list[float]]:
        """Return a relevance score per text, or None if the backend is unavailable."""
        if not texts:
            return []
        try:
            scores: list[float] = []
            for text in texts:
                key = _cache_key(query, text)
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    scores.append(cached)
                    continue
                prompt = f"Query: {query}\nDocument: {text}\nRelevance:"
                response = self._client.generate(
                    model=self.model,
                    prompt=prompt,
                    options={"temperature": 0},
                )
                raw = response.get("response", "") if isinstance(response, dict) else str(response)
                try:
                    value = float(raw.strip())
                except ValueError:
                    value = 0.0
                self._cache[key] = value
                self._cache.move_to_end(key)
                if len(self._cache) > MAX_CACHE_SIZE:
                    self._cache.popitem(last=False)
                scores.append(value)
            return scores
        except (ConnectionError, OSError, httpx.NetworkError) as exc:
            logger.warning("Reranker unavailable at %s: %s; falling back to RRF order.", self.base_url, exc)
            return None


class FakeReranker:
    """Deterministic reranker for CI: derives a stable score from sha256(query+text)."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [self._score_one(query, text) for text in texts]

    def _score_one(self, query: str, text: str) -> float:
        seed = int(hashlib.sha256((query + "\x00" + text).encode("utf-8")).hexdigest(), 16)
        return random.Random(seed).uniform(0.0, 1.0)


def _cache_key(query: str, text: str) -> str:
    return hashlib.sha256((query + "\x00" + text).encode("utf-8")).hexdigest()


def build_reranker(config: dict[str, object]) -> Optional[object]:
    """Build the configured reranker, or None if disabled."""
    rerank_cfg = cast(dict[str, object], (config.get("search", {}) or {}).get("rerank", {}) or {})
    if not rerank_cfg.get("enabled", False):
        return None
    if rerank_cfg.get("backend") == "fake":
        return FakeReranker()
    return OllamaReranker(config)


def create_reranker(
    config: Optional[dict[str, object]] = None,
    pool: Optional[asyncpg.Pool] = None,
) -> Reranker:
    """Return the reranker selected by ``search.reranker`` in config.

    - ``"none"`` (default): IdentityReranker — pass-through.
    - ``"pgml"``: PgmlReranker — cross-encoder reranking via the pool.
    - ``"ollama"``: OllamaReranker — qwen3-reranker scoring via Ollama.

    Raises ``ValueError`` for any other value.
    """
    cfg = config or load_config()
    search = cast(dict[str, object], cfg.get("search", {}))
    reranker = _str_or_default(search, "reranker", DEFAULT_RERANKER)
    if reranker == "none":
        return IdentityReranker()
    if reranker == "pgml":
        return PgmlReranker(cfg, pool)
    if reranker == "ollama":
        # OllamaReranker exposes the score-based pipeline interface rather
        # than the async Reranker protocol; callers selecting "ollama" use
        # the post-RRF scoring path in QueryHandler.
        return cast(Reranker, OllamaReranker(cfg))
    raise ValueError(
        f"Unknown search.reranker {reranker!r}: expected 'none', 'pgml', or 'ollama'"
    )
