"""Tests for the LlamaIndex RAG backend (no live Ollama required).

Skips honestly when the ``[llamaindex]`` extra is absent (CI never
installs it). The missing-extra behavior runs extras-less elsewhere: the
typed error in ``tests/test_error_taxonomy.py`` and the wiring degrade
path in ``tests/test_server_wiring_startup.py``, both through the
``_import_module`` seam.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("llama_index.core")

from corpus_kb.storage.llamaindex_backend import (
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
async def test_initialize_ollama_unreachable_degrades() -> None:
    """A dead Ollama port logs one warning; the store still boots degraded."""
    backend = LlamaIndexPostgresBackend(_config("http://localhost:65535", 768))
    fake_store = type("FakeStore", (), {"embed_dim": 768})()
    with patch(
        "llama_index.vector_stores.postgres.PGVectorStore.from_params",
        return_value=fake_store,
    ):
        await backend.initialize()


@pytest.mark.asyncio
async def test_dimension_mismatch_raises() -> None:
    """Configured dimensions that do not match the store raise DimensionMismatchError."""
    backend = LlamaIndexPostgresBackend(_config("http://localhost:11434", 512))
    backend._ollama_embedding = AsyncMock()  # type: ignore[assignment]
    fake_store = type("FakeStore", (), {"embed_dim": 768})()
    with (
        patch(
            "llama_index.vector_stores.postgres.PGVectorStore.from_params",
            return_value=fake_store,
        ),
        pytest.raises(DimensionMismatchError),
    ):
        await backend.initialize()
