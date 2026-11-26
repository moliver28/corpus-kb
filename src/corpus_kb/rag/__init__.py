"""RAG layer: embedders, search, and reranking components."""

from __future__ import annotations

from .embedder import FakeEmbedder, OllamaEmbedder, PgmlEmbedder
from .embedder_factory import aembed_batch, create_embedder, provider_label
from .reranker import IdentityReranker, PgmlReranker, create_reranker

__all__ = [
    "FakeEmbedder",
    "IdentityReranker",
    "OllamaEmbedder",
    "PgmlEmbedder",
    "PgmlReranker",
    "aembed_batch",
    "create_embedder",
    "create_reranker",
    "provider_label",
]
