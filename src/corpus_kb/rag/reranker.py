"""Rerankers for Corpus-KB.

IdentityReranker (legacy ``search.reranker: none`` selector): pass-through —
results are already fused by ``corpus.rrf_fusion()``.
PgmlReranker (legacy ``search.reranker: pgml`` selector): cross-encoder
reranking via ``pgml.rank()`` inside PostgreSQL.
OllamaReranker (``reranking.enabled: true``, the canonical gate; the
``search.reranker`` selector stays accepted as a legacy alias): scores
(query, chunk) pairs with the qwen3 judgment contract — that model family
has NO rerank endpoint and NO float score output; it answers a yes/no
relevance judgment.

W3 live verification against Ollama 0.31.1 (2026-10-15, qwen3:4b): the
transport path for token-logprob grading WORKS — ``Client.generate``
sends ``logprobs`` (bool) and ``top_logprobs`` (int, 0-20) as TOP-LEVEL
body fields and the server answers 200 with per-token alternatives (a
numeric ``logprobs`` is what 400s). Grading is still unusable there: the
qwen3 4B revision reasons before any verdict, so the judgment position
carries no yes/no mass within a bounded ``num_predict``, while
mid-reasoning echo words (the quoted instruction "yes or no") carry
P ~ 1.0 mass that poisons a max-mass scan. The live mode is therefore
``generative-binary``: one bounded generate per pair with ``think``
disabled, the verdict parsed from the response text, and a binary score
{yes: 1.0, no: 0.0} with a neutral 0.5 whenever no unambiguous verdict
appears (both words, or neither). True cross-encoder grading returns when
Ollama ships a rerank endpoint or stable logprobs.
FakeReranker: deterministic reranker for CI / degraded mode, mirroring
FakeEmbedder in corpus_kb/rag/embedder.py.
LlamaCppReranker (``reranking.backend: llamacpp``): true cross-encoder
scores via a llama.cpp ``/v1/rerank`` server (rag/rerank_llamacpp.py) —
the only backend that returns graded relevance floats today, since
Ollama 0.31.1 cannot serve reranker GGUFs in any mode.

On any pgml failure PgmlReranker logs a warning and returns the input
unchanged so callers can continue operating in degraded mode — reranking
must never break search. On any failure (connection, timeout, model not
pulled) OllamaReranker returns None (``not_evaluable``, not raised, not
zeros) so callers fall open to the pre-rerank RRF order — reranking is a
strict quality add-on, never a hard dependency of search. A pair whose
response text yields no unambiguous yes/no verdict scores a neutral 0.5
and is counted in ``last_not_evaluable`` for honesty reporting.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import OrderedDict
from typing import Protocol, cast, runtime_checkable

import asyncpg
import httpx
from ollama import Client, ResponseError

from ..config import load_config
from ..domain.models import SearchResult
from ..utils.deterministic import HashRandom
from .embedder import _str_or_default
from .rerank_settings import load_rerank_settings

logger = logging.getLogger(__name__)

DEFAULT_RERANKER = "none"
DEFAULT_RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RANK_TIMEOUT_SECONDS = 30.0
MAX_CACHE_SIZE = 10_000

# Mode reported by OllamaReranker (QueryHandler surfaces it in the search
# envelope's degraded_excluded.reasons so users know grading is coarser
# than a cross-encoder score).
RERANK_MODE_GENERATIVE_BINARY = "generative-binary"

# Bounded generation for one judgment (probed live on qwen3:4b: short
# reasoning fits, the verdict word lands inside the budget, and a rambling
# response truncates into the neutral-0.5 branch instead of running away).
GENERATIVE_JUDGMENT_TOKENS = 96

# The judgment prompt (unchanged since the U30 capture; the W3 generative
# path reads the verdict from the response TEXT, so the shape stays the
# same across model revisions).
RERANK_PROMPT_TEMPLATE = (
    "Judge whether the Document meets the requirements based on the Query. "
    "Reply with exactly one word, yes or no.\n"
    "Query: {query}\n"
    "Document: {document}\n"
    "Relevance:"
)

_VERDICT_RE = re.compile(r"\b(yes|no)\b")


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
    """qwen3 judgment scoring via bounded generative verdicts (U30 contract).

    qwen3-reranker has NO rerank endpoint and does not emit floats: it
    answers a yes/no relevance judgment. Each pair costs one short
    generate call with ``think`` disabled and a bounded ``num_predict``;
    the verdict is parsed from the response text and scored binary
    (yes = 1.0, no = 0.0). A response with no unambiguous verdict (both
    words present — e.g. the echoed instruction "yes or no" — or neither)
    is ``not_evaluable`` and scores a neutral 0.5.

    ``score`` returns None (``not_evaluable``) when the backend is
    unavailable — connection refused, timed out, or the configured rerank
    model is not pulled (``qwen3-reranker`` was NOT present on the registry
    at implementation time; the default degrades to un-reranked RRF order
    with a logged warning). ``rerank_mode`` reports
    ``generative-binary`` so callers can label the coarser grading.
    """

    def __init__(self, config: dict[str, object] | None = None) -> None:
        self._settings = load_rerank_settings(config or {})
        self.model = self._settings.model
        self.base_url = self._settings.base_url
        self._client = Client(host=self.base_url, timeout=self._settings.timeout_seconds)
        self._cache: OrderedDict[str, float] = OrderedDict()
        self.last_not_evaluable = 0
        self.rerank_mode = RERANK_MODE_GENERATIVE_BINARY

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
        """One bounded generate; binary verdict parse, None when ambiguous."""
        # ~4 chars per token keeps the document side of the pair inside
        # max_pair_tokens without a tokenizer dependency (documented
        # approximation; truncation is a recall knob, not a correctness one).
        max_chars = self._settings.max_pair_tokens * 4
        prompt = RERANK_PROMPT_TEMPLATE.format(query=query, document=text[:max_chars])
        response = self._generate(prompt)
        verdict = _parse_verdict(_response_text(response))
        if verdict == "yes":
            return 1.0
        if verdict == "no":
            return 0.0
        return None

    def _generate(self, prompt: str) -> object:
        """One bounded judgment generate; ``think`` off for qwen3 verdicts.

        qwen3 models otherwise spend the whole budget inside the thinking
        channel (the response stays empty). A server or model that rejects
        the ``think`` parameter gets ONE retry without it, so the generative
        path also works where thinking is unsupported.
        """
        options: dict[str, object] = {
            "temperature": 0,
            "num_predict": GENERATIVE_JUDGMENT_TOKENS,
        }
        try:
            return self._client.generate(
                model=self.model,
                prompt=prompt,
                stream=False,
                think=False,
                options=options,
            )
        except ResponseError as exc:
            if "think" not in str(exc).lower():
                raise
            logger.debug("Reranker retry without think (server rejected it): %s", exc)
            return self._client.generate(
                model=self.model,
                prompt=prompt,
                stream=False,
                options=options,
            )


def _parse_verdict(text: str) -> str | None:
    """The pair's verdict: 'yes'/'no' when EXACTLY one verdict word appears.

    Deliberately conservative: reasoning models echo the instruction
    ("reply with exactly one word, yes or no"), so both words present means
    no readable verdict — None (neutral 0.5), never a guess. Case and
    punctuation around the word are ignored.
    """
    words = set(_VERDICT_RE.findall(text.lower()))
    if len(words) == 1:
        return words.pop()
    return None


def _response_text(response: object) -> str:
    """Read the generated text off a pydantic GenerateResponse or dict."""
    raw = getattr(response, "response", None)
    if raw is None and isinstance(response, dict):
        raw = response.get("response")
    return raw if isinstance(raw, str) else ""


class FakeReranker:
    """Deterministic reranker for CI: derives a stable score from sha256(query+text)."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [self._score_one(query, text) for text in texts]

    def _score_one(self, query: str, text: str) -> float:
        seed = int(hashlib.sha256((query + "\x00" + text).encode("utf-8")).hexdigest(), 16)
        return HashRandom(seed).uniform(0.0, 1.0)


def _cache_key(query: str, text: str) -> str:
    return hashlib.sha256((query + "\x00" + text).encode("utf-8")).hexdigest()


def build_reranker(config: dict[str, object]) -> object | None:
    """Build the configured reranker, or None if disabled (U30 production gate).

    ``reranking.backend`` selects the scorer: ``"llamacpp"`` builds the
    true cross-encoder client, ``"fake"`` the test double, and
    null/``"generative"``/absent the qwen3 judgment scorer (OllamaReranker).
    """
    if not load_rerank_settings(config).enabled:
        return None
    reranking = cast(dict[str, object], (config.get("reranking", {}) or {}))
    if reranking.get("backend") == "fake":
        return FakeReranker()
    if reranking.get("backend") == "llamacpp":
        # Function-local import: rerank_llamacpp shares the cache helpers
        # defined in this module, so the dependency is one-way at runtime.
        from .rerank_llamacpp import LlamaCppReranker

        return LlamaCppReranker(config)
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
