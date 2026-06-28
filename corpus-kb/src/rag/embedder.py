"""Ollama-backed embedder with SHA256-keyed LRU cache and graceful degradation."""

from __future__ import annotations

import hashlib
import logging
import random
from collections import OrderedDict
from typing import Optional, cast

import httpx
from ollama import Client
from ollama._types import EmbedResponse

from src.config import load_config

logger = logging.getLogger(__name__)

MAX_CACHE_SIZE = 10_000


class OllamaEmbedder:
    """Embedder that calls a local Ollama instance, caching results in memory.

    On connection failure the embedder logs a warning and returns zero-vectors
    so callers can continue operating in degraded mode.
    """

    def __init__(self, config: Optional[dict[str, object]] = None) -> None:
        self._config = config or load_config()
        embedding = cast(dict[str, object], self._config.get("embedding", {}))

        self.model = _str_or_default(embedding, "model", "nomic-embed-text")
        self.base_url = _str_or_default(embedding, "base_url", "http://localhost:11434")
        self.batch_size = _int_or_default(embedding, "batch_size", 32)
        self.dimensions = _int_or_default(embedding, "dimensions", 768)

        self._client = Client(host=self.base_url)
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    def embed(self, text: str) -> list[float]:
        """Return a single embedding vector for ``text``."""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embedding vectors for ``texts``, using the cache and batching."""
        if not texts:
            return []

        keys = [_sha256_key(text) for text in texts]
        results: list[list[float]] = [[] for _ in texts]
        missing: list[tuple[int, str]] = []

        for idx, key in enumerate(keys):
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                results[idx] = cached
            else:
                missing.append((idx, texts[idx]))

        if not missing:
            return results

        missing_texts = [text for _, text in missing]
        batch_size = max(1, self.batch_size)
        batched_results: list[list[float]] = []

        for start in range(0, len(missing_texts), batch_size):
            batch = missing_texts[start : start + batch_size]
            batched_results.extend(self._embed_batch(batch))

        for (idx, text), vector in zip(missing, batched_results):
            key = _sha256_key(text)
            self._cache[key] = vector
            self._cache.move_to_end(key)
            if len(self._cache) > MAX_CACHE_SIZE:
                self._cache.popitem(last=False)
            results[idx] = vector

        return results

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            response: EmbedResponse = self._client.embed(
                model=self.model,
                input=texts,
            )
        except (ConnectionError, OSError, httpx.NetworkError):
            logger.warning(
                "Ollama connection failed at %s; returning zero vectors.",
                self.base_url,
            )
            return [[0.0] * self.dimensions for _ in texts]

        return [list(vector) for vector in response.embeddings]


def _sha256_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _str_or_default(config: dict[str, object], key: str, default: str) -> str:
    value = config.get(key, default)
    return str(value) if value is not None else default


def _int_or_default(config: dict[str, object], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return default


class FakeEmbedder:
    """Deterministic embedder that does not require Ollama.

    Useful for CI or local runs where the embedding service is unavailable.
    Each vector is deterministically derived from the SHA256 hash of the text
    and has length ``dimensions``.
    """

    def __init__(self, config: Optional[dict[str, object]] = None) -> None:
        embedding = cast(
            dict[str, object],
            (config or load_config()).get("embedding", {}),
        )
        self.dimensions = _int_or_default(embedding, "dimensions", 768)

    def embed(self, text: str) -> list[float]:
        """Return a deterministic vector for ``text``."""
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic vectors for ``texts``."""
        return [self._vector_for(text) for text in texts]

    def _vector_for(self, text: str) -> list[float]:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(self.dimensions)]
