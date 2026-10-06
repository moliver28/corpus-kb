"""Live-Ollama proof of the research embed boundary (todo-11, requires_ollama).

Ground truth on this host: qwen3-embedding:8b-q8_0 emits 4096 dims; the
research boundary MRL-slice-normalizes to EXACTLY 1024 with unit norm and
round-trips through the write-once embedding_cache (second call is a cache
hit: zero embedder invocations, asserted via a call counter).
"""

from __future__ import annotations

import socket

import pytest
from research_db import TEST_TENANT

from corpus_kb.projections.research._embed import RESEARCH_DIMENSIONS, ResearchEmbedder
from corpus_kb.rag.embedder import OllamaEmbedder

pytestmark = [pytest.mark.requires_postgres, pytest.mark.requires_ollama]

OLLAMA_URL = "http://localhost:11434"


def _ollama_up() -> bool:
    try:
        with socket.create_connection(("localhost", 11434), timeout=2):
            return True
    except OSError:
        return False


async def test_qwen3_slice_is_exactly_1024_and_cached(research_pool):
    if not _ollama_up():
        pytest.skip("Ollama not reachable")

    calls = {"n": 0}

    class Counting(OllamaEmbedder):
        def embed_batch(self, texts):  # type: ignore[override]
            calls["n"] += 1
            return super().embed_batch(texts)

    raw = Counting(
        {
            "embedding": {
                "model": "qwen3-embedding:8b-q8_0",
                "base_url": OLLAMA_URL,
                "dimensions": 4096,
                "batch_size": 4,
            }
        }
    )
    embedder = ResearchEmbedder(research_pool, raw, model_revision="qwen3-8b-mrl1024")
    from uuid import UUID

    tenant = UUID(TEST_TENANT)
    text = "Support responded within the hour and built real trust."

    vector = await embedder.embed_cached(tenant, text)
    assert vector is not None, "qwen3 4096-dim must slice to 1024, not abstain"
    assert len(vector) == RESEARCH_DIMENSIONS
    norm = sum(x * x for x in vector) ** 0.5
    assert abs(norm - 1.0) < 0.02, "vector must be unit-normalized"

    calls_after_first = calls["n"]
    again = await embedder.embed_cached(tenant, text)
    assert again is not None
    assert again == pytest.approx(vector, abs=1e-6), "cache must return the same vector"
    assert calls["n"] == calls_after_first, "cache hit must make zero embedder calls"
