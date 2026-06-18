"""Ollama-backed embedding service with batching and caching.

Provides high-level embed() and embed_batch() methods used by the ingestion
pipeline and hybrid search layer. Automatically batches requests, caches
results in-memory, and handles Ollama connectivity errors gracefully.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from utils.models import Chunk

# Default embedding model and dimensions
DEFAULT_MODEL = "qwen3-embedding:8b-q8_0"
DEFAULT_DIMENSIONS = 4096


class OllamaEmbedder:
    """Embedding service wrapping Ollama's embed API.

    Usage:
        embedder = OllamaEmbedder()
        vector = embedder.embed("Hello world")       # single string
        vectors = embedder.embed_batch(["a", "b"])    # batched
        embedder.embed_chunks(chunks)                 # in-place on Chunk list
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        batch_size: int = 10,
        cache_size: int = 10_000,
    ):
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._cache: dict[str, list[float]] = {}
        self._cache_max = cache_size

    def embed(self, text: str) -> list[float]:
        """Embed a single text string. Returns a 768-d vector."""
        cache_key = self._cache_key(text)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            import ollama
            resp = ollama.embed(model=self.model, input=text)
            vector = resp.embeddings[0]
        except Exception:
            # Return zero vector on failure (graceful degradation)
            vector = [0.0] * self.dimensions

        # Cache with LRU-like eviction
        if len(self._cache) >= self._cache_max:
            self._cache.clear()
        self._cache[cache_key] = vector
        return vector

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns list of vectors in same order."""
        if not texts:
            return []

        vectors: list[list[float]] = []
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []

        for i, text in enumerate(texts):
            cache_key = self._cache_key(text)
            cached = self._cache.get(cache_key)
            if cached is not None:
                vectors.append(cached)
            else:
                vectors.append([])  # placeholder
                uncached_texts.append(text)
                uncached_indices.append(i)

        # Batch-embed any uncached texts
        if uncached_texts:
            try:
                import ollama
                for start in range(0, len(uncached_texts), self.batch_size):
                    batch = uncached_texts[start:start + self.batch_size]
                    resp = ollama.embed(model=self.model, input=batch)
                    for j, vector in enumerate(resp.embeddings):
                        idx = uncached_indices[start + j]
                        vectors[idx] = vector
                        cache_key = self._cache_key(batch[j])
                        if len(self._cache) < self._cache_max:
                            self._cache[cache_key] = vector
            except Exception:
                # Fill remaining with zeros
                for idx in uncached_indices:
                    vectors[idx] = [0.0] * self.dimensions

        return vectors

    def embed_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Embed chunk texts in-place, populating chunk.vector."""
        texts = [c.text for c in chunks]
        vectors = self.embed_batch(texts)
        for chunk, vector in zip(chunks, vectors):
            chunk.vector = vector
        return chunks

    def _cache_key(self, text: str) -> str:
        """Generate a deterministic cache key from text."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def clear_cache(self):
        """Clear the in-memory embedding cache."""
        self._cache.clear()
