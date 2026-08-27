"""Cross-encoder rerankers for Corpus-KB.

OllamaReranker: scores (query, chunk) pairs via a local Ollama generate
call using the qwen3-reranker prompt format, confirmed as
"Query: {query}\nDocument: {doc}\nRelevance:" against ollama.generate().
FakeReranker: deterministic reranker for CI / degraded mode, mirroring
FakeEmbedder in src/rag/embedder.py.

On any failure OllamaReranker returns None (not raised, not zeros) so
callers can fall open to the pre-rerank RRF order -- reranking is a
strict quality add-on, never a hard dependency of search.
"""

from __future__ import annotations

import hashlib
import logging
import random
from collections import OrderedDict
from typing import Optional, cast

import httpx
from ollama import Client

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 10_000


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
