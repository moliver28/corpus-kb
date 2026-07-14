"""RAG layer: embedders, search, and reranking components."""

from __future__ import annotations

from .embedder import FakeEmbedder, OllamaEmbedder

__all__ = ["FakeEmbedder", "OllamaEmbedder"]
