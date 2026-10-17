"""True cross-encoder reranking via a llama.cpp ``/v1/rerank`` server.

Ollama 0.31.1 ships no rerank endpoint and refuses reranker GGUFs on the
generate route, so rerankers cannot run through it (verified live; see
rag/reranker.py for the generative fallback contract). The ecosystem
standard path is a rank-capable HTTP server — llama.cpp ``llama-server``,
TEI, or vLLM. All expose ``POST /v1/rerank`` with a ``{model, query,
documents}`` body and answer ``{"results": [{"index", "relevance_score"},
...]}`` with true graded scores.

LlamaCppReranker posts one batch per ``score()`` call (cache misses only,
so a warm cache never re-sends a pair) and maps the returned entries back
to the input order by ``index`` — never by response position. A missing
entry scores the neutral 0.5 and counts in ``last_not_evaluable`` for
honesty reporting, same as an ambiguous judgment in OllamaReranker.

Fail-open contract (identical to OllamaReranker): connection refused,
timeout, HTTP error, or a non-JSON body logs ONE warning and returns None
(``not_evaluable`` — not raised, not zeros), so callers fall open to the
pre-rerank RRF order. Reranking is a strict quality add-on, never a hard
dependency of search.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import cast

import httpx

from .rerank_settings import load_rerank_settings
from .reranker import MAX_CACHE_SIZE, _cache_key

logger = logging.getLogger(__name__)

# Mode reported by LlamaCppReranker: true graded cross-encoder scores, so
# QueryHandler adds no generative-binary honesty note for this backend.
RERANK_MODE_CROSS_ENCODER = "cross-encoder"

# Neutral score for pairs the server left out of a 200 response (same
# neutral the generative path uses for not-evaluable judgments).
NEUTRAL_SCORE = 0.5


class LlamaCppReranker:
    """Cross-encoder scoring against a ``/v1/rerank`` HTTP endpoint.

    ``score`` returns one relevance score per input text (graded, not
    binary), or None when the backend is unavailable — fail-open to the
    fused RRF order. Documents are truncated client-side at
    ``max_pair_tokens * 4`` chars (the same documented ~4 chars/token
    approximation OllamaReranker uses; truncation is a recall knob, not a
    correctness one). Successful scores are cached LRU-style keyed on the
    (query, text) pair.
    """

    def __init__(self, config: dict[str, object] | None = None) -> None:
        self._settings = load_rerank_settings(config or {})
        self.model = self._settings.model
        self.base_url = self._settings.llamacpp_url
        self._cache: OrderedDict[str, float] = OrderedDict()
        self.last_not_evaluable = 0
        self.rerank_mode = RERANK_MODE_CROSS_ENCODER

    def score(self, query: str, texts: list[str]) -> list[float] | None:
        """Return a relevance score per text, or None if the backend is unavailable."""
        self.last_not_evaluable = 0
        if not texts:
            return []
        max_chars = self._settings.max_pair_tokens * 4
        scores: list[float | None] = [None] * len(texts)
        pending: list[tuple[int, str]] = []
        for position, text in enumerate(texts):
            key = _cache_key(query, text)
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                scores[position] = cached
                continue
            pending.append((position, text[:max_chars]))
        if pending:
            fresh = self._score_batch(query, [doc for _, doc in pending])
            if fresh is None:
                return None
            for (position, text), value in zip(pending, fresh, strict=True):
                scores[position] = value
                self._remember(_cache_key(query, text), value)
        if self.last_not_evaluable:
            logger.warning(
                "llama.cpp rerank left %d/%d pairs unscored; neutral 0.5 used.",
                self.last_not_evaluable,
                len(texts),
            )
        return [NEUTRAL_SCORE if value is None else value for value in scores]

    def _remember(self, key: str, value: float) -> None:
        """LRU insert with the shared 10k cap (rag/reranker.py contract)."""
        self._cache[key] = value
        self._cache.move_to_end(key)
        if len(self._cache) > MAX_CACHE_SIZE:
            self._cache.popitem(last=False)

    def _score_batch(self, query: str, documents: list[str]) -> list[float] | None:
        """One POST /v1/rerank; None (fail-open) on any transport or HTTP error."""
        url = f"{self.base_url}/v1/rerank"
        payload = {"model": self.model, "query": query, "documents": documents}
        try:
            response = httpx.post(url, json=payload, timeout=self._settings.timeout_seconds)
            response.raise_for_status()
            body: object = response.json()
        except (httpx.HTTPError, OSError, ValueError) as exc:
            # httpx.HTTPError covers transport failures and 4xx/5xx via
            # raise_for_status; ValueError covers a non-JSON 200 body.
            logger.warning(
                "llama.cpp reranker unavailable at %s (model %s): %s; falling back to RRF order.",
                self.base_url,
                self.model,
                exc,
            )
            return None
        return self._scores_from(body, len(documents))

    def _scores_from(self, body: object, expected: int) -> list[float]:
        """Align ``results`` entries to input positions by index; missing → 0.5."""
        scores: list[float | None] = [None] * expected
        entries: object = body.get("results") if isinstance(body, dict) else None
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                parsed = _parse_entry(cast("dict[object, object]", entry), expected)
                if parsed is not None:
                    scores[parsed[0]] = parsed[1]
        self.last_not_evaluable += sum(1 for value in scores if value is None)
        return [NEUTRAL_SCORE if value is None else value for value in scores]


def _parse_entry(entry: dict[object, object], expected: int) -> tuple[int, float] | None:
    """One ``{"index", "relevance_score"}`` entry, or None when unusable.

    Out-of-range indexes and non-numeric scores count as missing entries
    (the caller scores them neutral) rather than a hard failure.
    """
    index = entry.get("index")
    value = entry.get("relevance_score")
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not 0 <= index < expected:
        return None
    return index, float(value)


__all__ = ["RERANK_MODE_CROSS_ENCODER", "LlamaCppReranker"]
