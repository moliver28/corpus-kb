"""Rerankers for Corpus-KB.

IdentityReranker (``search.reranker: none``, the default): pass-through —
results are already fused by ``corpus.rrf_fusion()``.
PgmlReranker (``search.reranker: pgml``): cross-encoder reranking via
``pgml.rank()`` inside PostgreSQL.
OllamaReranker (``search.reranker: ollama`` or ``search.rerank.enabled``):
scores (query, chunk) pairs with the qwen3-reranker judgment contract —
that model family has NO rerank endpoint and NO float score output; it
answers a yes/no relevance judgment, so the score is
``P("yes") / (P("yes") + P("no"))`` computed from the TOKEN LOGPROBS of the
judgment position (verified live against Ollama 0.31.1: ``/api/generate``
accepts ``logprobs=true`` plus ``top_logprobs=<k>`` and returns per-token
logprob arrays with ranked alternatives; ``logprobs`` as a number is
rejected — it is bool-typed server-side).
FakeReranker: deterministic reranker for CI / degraded mode, mirroring
FakeEmbedder in corpus_kb/rag/embedder.py.

On any pgml failure PgmlReranker logs a warning and returns the input
unchanged so callers can continue operating in degraded mode — reranking
must never break search. On any failure (connection, timeout, model not
pulled) OllamaReranker returns None (``not_evaluable``, not raised, not
zeros) so callers fall open to the pre-rerank RRF order — reranking is a
strict quality add-on, never a hard dependency of search. A judgment
position where neither yes nor no mass appears scores a neutral 0.5 and is
counted in ``last_not_evaluable`` for honesty reporting.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from collections import OrderedDict
from typing import Protocol, cast, runtime_checkable

import asyncpg
import httpx
from ollama import Client, ResponseError

from ..config import load_config
from ..domain.models import SearchResult
from .embedder import _str_or_default
from .rerank_settings import load_rerank_settings

logger = logging.getLogger(__name__)

DEFAULT_RERANKER = "none"
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RANK_TIMEOUT_SECONDS = 30.0
MAX_CACHE_SIZE = 10_000

# U30 judgment-scoring constants. TOP_LOGPROBS_K wide enough to capture the
# yes/no variants (" yes", " Yes", "yes." ...) at the judgment position;
# MAX_JUDGMENT_TOKENS bounds the generate so a rambling model cannot turn
# one rerank score into a full completion.
TOP_LOGPROBS_K = 20
MAX_JUDGMENT_TOKENS = 8

# Verified against Ollama 0.31.1 (2026-10-10): this exact prompt shape makes
# the judgment the first word-bearing generated token — a leading-space token
# is common, so the scorer scans positions and takes the max yes/no mass.
RERANK_PROMPT_TEMPLATE = (
    "Judge whether the Document meets the requirements based on the Query. "
    "Reply with exactly one word, yes or no.\n"
    "Query: {query}\n"
    "Document: {document}\n"
    "Relevance:"
)


@runtime_checkable
class Reranker(Protocol):
    """Protocol for search-result rerankers."""

    async def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        """Return ``results`` re-ranked by relevance to ``query``."""
        ...


class IdentityReranker:
    """Pass-through reranker — returns the fused results unchanged."""

    async def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
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
        config: dict[str, object] | None = None,
        pool: asyncpg.Pool | None = None,
    ) -> None:
        self._config = config or load_config()
        search = cast(dict[str, object], self._config.get("search", {}))
        self.model = _str_or_default(search, "reranker_model", DEFAULT_RERANKER_MODEL)
        self._pool = pool

    async def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
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
        scores: list[float | None] = [None] * len(results)
        for row in rows:
            idx = int(row["corpus_id"])
            if 0 <= idx < len(results):
                scores[idx] = float(row["score"])
        matched = sum(score is not None for score in scores)
        if matched != len(results):
            logger.warning(
                "pgml.rank() returned scores for %d of %d results; returning unreranked results.",
                matched,
                len(results),
            )
            return results

        order = sorted(range(len(results)), key=lambda i: cast(float, scores[i]), reverse=True)
        return [results[i] for i in order]


class OllamaReranker:
    """qwen3-reranker scoring via Ollama token logprobs (U30 contract).

    qwen3-reranker has NO rerank endpoint and does not emit floats: it
    answers a yes/no relevance judgment. The score is therefore
    ``P(yes) / (P(yes) + P(no))`` computed from the token logprobs at the
    judgment position. Each pair costs one short generate call with
    ``logprobs=true`` and ``top_logprobs`` alternatives.

    ``score`` returns None (``not_evaluable``) when the backend is
    unavailable — connection refused, timed out, or the configured rerank
    model is not pulled (``qwen3-reranker`` was NOT present on the registry
    at implementation time; the default degrades to un-reranked RRF order
    with a logged warning). Pairs whose judgment position carries neither
    yes nor no mass score a neutral 0.5 and are counted in
    ``last_not_evaluable``.
    """

    def __init__(self, config: dict[str, object] | None = None) -> None:
        self._settings = load_rerank_settings(config or {})
        self.model = self._settings.model
        self.base_url = self._settings.base_url
        self._client = Client(host=self.base_url, timeout=self._settings.timeout_seconds)
        self._cache: OrderedDict[str, float] = OrderedDict()
        self.last_not_evaluable = 0

    def score(self, query: str, texts: list[str]) -> list[float] | None:
        """Return a relevance score per text, or None if the backend is unavailable."""
        self.last_not_evaluable = 0
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
                value = self._score_pair(query, text)
                if value is None:
                    self.last_not_evaluable += 1
                    value = 0.5
                self._cache[key] = value
                self._cache.move_to_end(key)
                if len(self._cache) > MAX_CACHE_SIZE:
                    self._cache.popitem(last=False)
                scores.append(value)
            if self.last_not_evaluable:
                logger.warning(
                    "qwen3 rerank judgment not evaluable for %d/%d pairs; neutral 0.5 used.",
                    self.last_not_evaluable,
                    len(texts),
                )
            return scores
        except (
            ConnectionError,
            OSError,
            httpx.NetworkError,
            httpx.TimeoutException,
            ResponseError,
        ) as exc:
            # httpx.TimeoutException is a sibling of NetworkError under
            # TransportError and needs its own entry: an expired client bound
            # must degrade exactly like a refused connection. ResponseError
            # covers "model not found" (qwen3-reranker not pulled).
            logger.warning(
                "Reranker unavailable at %s (model %s): %s; falling back to RRF order.",
                self.base_url,
                self.model,
                exc,
            )
            return None

    def _score_pair(self, query: str, text: str) -> float | None:
        """One generate call; P(yes)/(P(yes)+P(no)) from judgment logprobs."""
        # ~4 chars per token keeps the document side of the pair inside
        # max_pair_tokens without a tokenizer dependency (documented
        # approximation; truncation is a recall knob, not a correctness one).
        max_chars = self._settings.max_pair_tokens * 4
        prompt = RERANK_PROMPT_TEMPLATE.format(query=query, document=text[:max_chars])
        response = self._client.generate(
            model=self.model,
            prompt=prompt,
            stream=False,
            options={"temperature": 0, "num_predict": MAX_JUDGMENT_TOKENS},
            logprobs=True,
            top_logprobs=TOP_LOGPROBS_K,
        )
        entries = _logprob_entries(response)
        return _judgment_score(entries)


class _LogprobAlt:
    """One ranked alternative token at a generation position."""

    __slots__ = ("logprob", "token")

    def __init__(self, token: str, logprob: float) -> None:
        self.token = token
        self.logprob = logprob


def _logprob_entries(response: object) -> list[list[_LogprobAlt]]:
    """Extract per-position top-logprob alternatives from a generate response.

    The ollama client returns pydantic models; dict fallbacks mirror the
    coder_client contract. Each entry: ``.token/.logprob`` plus ranked
    ``.top_logprobs`` alternatives (verified server behavior on 0.31.1).
    The server includes the greedy token as the FIRST alternative, so tokens
    are de-duplicated per position (first occurrence wins) — counting the
    greedy token twice would inflate its side of the yes/no ratio.
    """
    raw_entries: object = getattr(response, "logprobs", None)
    if raw_entries is None and isinstance(response, dict):
        raw_entries = response.get("logprobs")
    if not isinstance(raw_entries, (list, tuple)):
        return []
    positions: list[list[_LogprobAlt]] = []
    for entry in raw_entries:
        greedy_token = str(_field(entry, "token") or "")
        greedy_lp = float(_field(entry, "logprob") or 0.0)
        alts: list[_LogprobAlt] = [_LogprobAlt(greedy_token, greedy_lp)]
        seen = {greedy_token}
        raw_alts = _field(entry, "top_logprobs")
        if not isinstance(raw_alts, (list, tuple)):
            positions.append(alts)
            continue
        for alt in raw_alts:
            token = str(_field(alt, "token") or "")
            if token in seen:
                continue
            seen.add(token)
            alts.append(_LogprobAlt(token, float(_field(alt, "logprob") or 0.0)))
        positions.append(alts)
    return positions


def _field(entry: object, name: str) -> object:
    """Read ``name`` off a pydantic logprob entry or its dict fallback."""
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def _normalize_judgment(token: str) -> str:
    """Normalize a token to its judgment word: strip space/punctuation, lower."""
    return token.strip().lower().strip(".,!?:;\"'`")


def _judgment_score(positions: list[list[_LogprobAlt]]) -> float | None:
    """P(yes)/(P(yes)+P(no)) at the position with the most yes/no mass.

    Models sometimes emit a leading-space or preamble token first, so every
    generated position is scored and the one concentrating yes/no judgment
    mass wins. None (not_evaluable) when no position carries any yes or no
    probability at all.
    """
    best: tuple[float, float, float] | None = None
    for alts in positions:
        p_yes = 0.0
        p_no = 0.0
        for alt in alts:
            judgment = _normalize_judgment(alt.token)
            if judgment == "yes":
                p_yes += math.exp(alt.logprob)
            elif judgment == "no":
                p_no += math.exp(alt.logprob)
        total = p_yes + p_no
        if total > 0.0 and (best is None or total > best[0]):
            best = (total, p_yes, p_no)
    if best is None:
        return None
    _, p_yes, p_no = best
    return p_yes / (p_yes + p_no)


class FakeReranker:
    """Deterministic reranker for CI: derives a stable score from sha256(query+text)."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [self._score_one(query, text) for text in texts]

    def _score_one(self, query: str, text: str) -> float:
        seed = int(hashlib.sha256((query + "\x00" + text).encode("utf-8")).hexdigest(), 16)
        return random.Random(seed).uniform(0.0, 1.0)


def _cache_key(query: str, text: str) -> str:
    return hashlib.sha256((query + "\x00" + text).encode("utf-8")).hexdigest()


def build_reranker(config: dict[str, object]) -> object | None:
    """Build the configured reranker, or None if disabled (U30 production gate)."""
    if not load_rerank_settings(config).enabled:
        return None
    rerank_cfg = cast(dict[str, object], (config.get("search", {}) or {}).get("rerank", {}) or {})
    if rerank_cfg.get("backend") == "fake":
        return FakeReranker()
    return OllamaReranker(config)


def create_reranker(
    config: dict[str, object] | None = None,
    pool: asyncpg.Pool | None = None,
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
    raise ValueError(f"Unknown search.reranker {reranker!r}: expected 'none', 'pgml', or 'ollama'")
