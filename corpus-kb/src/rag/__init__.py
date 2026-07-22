"""RAG layer: embedders, search, and reranking components."""

from __future__ import annotations

from .embedder import (
    FakeEmbedder,
    OllamaEmbedder,
    PgmlEmbedder,
    aembed_batch,
    create_embedder,
)

__all__ = [
    "FakeEmbedder",
    "OllamaEmbedder",
    "PgmlEmbedder",
    "aembed_batch",
    "create_embedder",
]
