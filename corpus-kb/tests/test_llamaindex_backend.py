"""Tests for the LlamaIndex RAG backend (no live Ollama required)."""

from __future__ import annotations

import pytest

from src.storage.llamaindex_backend import (
    DimensionMismatchError,
    LlamaIndexPostgresBackend,
)


def _config(base_url: str, dimensions: int) -> dict:
    return {
        "database": {
            "connection_string": "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
        },
        "embedding": {
            "model": "nomic-embed-text",
            "base_url": base_url,
            "dimensions": dimensions,
        },
    }


@pytest.mark.asyncio
async def test_initialize_ollama_unreachable_raises() -> None:
    """Pointing the backend at a dead Ollama port raises RuntimeError."""
    backend = LlamaIndexPostgresBackend(_config("http://localhost:65535", 768))
    with pytest.raises(RuntimeError):
        await backend.initialize()


@pytest.mark.asyncio
async def test_dimension_mismatch_raises() -> None:
    """Configured dimensions that do not match the store raise DimensionMismatchError."""
    backend = LlamaIndexPostgresBackend(_config("http://localhost:11434", 512))
    # Patch the Ollama check so we reach the PGVectorStore creation step.
    backend._ollama_embedding = _FakeEmbedder()  # type: ignore[assignment]
    with pytest.raises(DimensionMismatchError):
        await backend.initialize()


class _FakeEmbedder:
    """Stand-in OllamaEmbedding that never makes network calls."""

    async def aget_text_embedding(self, text: str) -> list[float]:
        return [0.0] * 768
