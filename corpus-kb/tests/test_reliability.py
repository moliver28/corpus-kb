"""Reliability tests for startup and embedding fallback."""

from __future__ import annotations

import pytest

from src.server_wiring import startup
from src.tools.ingest_common import embed_chunks
from src.utils.models import Chunk


@pytest.mark.asyncio
async def test_postgres_unavailable_raises() -> None:
    """startup() raises RuntimeError when the connection string is invalid."""
    with pytest.raises(RuntimeError):
        await startup(
            {"database": {"connection_string": "postgresql://bad:bad@localhost:1/none"}}
        )


def test_ollama_unavailable_degraded() -> None:
    """embed_chunks returns (True, error_string) when Ollama is unreachable."""
    chunks = [Chunk(text="hello", source_type="text", document_id="doc-1")]
    degraded, message = embed_chunks(
        chunks,
        {
            "embedding": {
                "provider": "ollama",
                "model": "nomic-embed-text",
                "base_url": "http://localhost:65535",
                "batch_size": 1,
                "dimensions": 768,
            }
        },
    )
    assert degraded is True
    assert message is not None
    assert "Error" in message or "Connection" in message or "Failed" in message
