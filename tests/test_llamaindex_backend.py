"""Tests for the LlamaIndex RAG backend (no live Ollama required)."""

from __future__ import annotations

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
    backend._ollama_embedding = AsyncMock()  # type: ignore[assignment]
    fake_store = type("FakeStore", (), {"embed_dim": 768})()
    with patch(
        "src.storage.llamaindex_backend.PGVectorStore.from_params",
        return_value=fake_store,
    ):
        with pytest.raises(DimensionMismatchError):
            await backend.initialize()
