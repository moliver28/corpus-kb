"""With-extra proofs for the late-chunking embedder (todo 13 acceptance).

* smoke (no DB): one forward pass emits 1024-dim UNIT-NORM vectors for all
  three exchange spans, and the embedder satisfies the runtime-checkable
  master Embedder Protocol;
* cache round-trip (requires_postgres): routing LateChunkEmbedder through
  the ResearchEmbedder boundary — the second identical embed is a cache
  hit with ZERO embedder invocations (call counter), and a zero-vector
  embed attempt is REJECTED (boundary abstains, no cache row appears).

CI never installs the latechunk extra: everything here skips there. The
smoke downloads/loads Qwen3-Embedding-0.6B on first local run.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

import pytest
from research_db import TEST_TENANT

from corpus_kb.projections.research._embed import RESEARCH_DIMENSIONS, ResearchEmbedder
from corpus_kb.rag.embedder import Embedder
from corpus_kb.rag.embedders import LateChunkEmbedder, is_latechunk_installed

pytestmark = [
    pytest.mark.skipif(
        not is_latechunk_installed(), reason="latechunk extra not installed (CI never installs it)"
    ),
]

TENANT_UUID = UUID(TEST_TENANT)


def test_late_chunk_smoke_emits_1024_unit_norm_spans():
    embedder = LateChunkEmbedder(device="cpu")
    assert embedder.dimensions == RESEARCH_DIMENSIONS
    assert isinstance(embedder, Embedder), "must satisfy the master Embedder Protocol"

    vectors = embedder.embed_exchange(
        "How painful was the password reset flow?",
        "The reset email arrived late twice and locked me out of login.",
    )
    assert embedder.forward_calls == 1, "one forward pass must yield all three spans"
    for name in ("question", "qa", "answer"):
        vec = getattr(vectors, name)
        assert len(vec) == RESEARCH_DIMENSIONS, name
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 0.02, f"{name} span must be unit-normalized, got {norm}"

    single = embedder.embed("standalone text")
    assert len(single) == RESEARCH_DIMENSIONS
    assert abs(sum(x * x for x in single) ** 0.5 - 1.0) < 0.02


class _ZeroLateChunk(LateChunkEmbedder):
    """Degraded-mode double: no model load, every embed returns zeros."""

    def __init__(self) -> None:
        self.dimensions = RESEARCH_DIMENSIONS
        self.model = "zero-stub"
        self.forward_calls = 0

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * RESEARCH_DIMENSIONS for _ in texts]


def _counting_embedder() -> tuple[LateChunkEmbedder, list[int]]:
    embedder = LateChunkEmbedder(device="cpu")
    calls: list[int] = []
    real = embedder.embed_batch

    def counting(texts: list[str]) -> list[list[float]]:
        calls.append(len(texts))
        return real(texts)

    embedder.embed_batch = counting  # type: ignore[method-assign]
    return embedder, calls


async def test_latechunk_cache_roundtrip_and_zero_vector_rejection(research_pool):
    embedder, calls = _counting_embedder()
    boundary = ResearchEmbedder(research_pool, embedder, model_revision="qwen3-0.6b-latechunk-1024")
    text = "Late-chunking span vectors must round-trip through the write-once cache."

    first = await boundary.embed_cached(TENANT_UUID, text)
    assert first is not None, "1024-dim late-chunk vector must not abstain"
    assert len(first) == RESEARCH_DIMENSIONS
    assert abs(sum(x * x for x in first) ** 0.5 - 1.0) < 0.02
    calls_after_first = len(calls)

    second = await boundary.embed_cached(TENANT_UUID, text)
    assert second is not None
    assert second == pytest.approx(first, abs=1e-6)
    assert len(calls) == calls_after_first, "cache hit = zero embedder calls"

    zero_boundary = ResearchEmbedder(
        research_pool, _ZeroLateChunk(), model_revision="qwen3-0.6b-latechunk-1024"
    )
    zero_text = "a degraded zero-vector embed must never reach the cache"
    rejected = await zero_boundary.embed_cached(TENANT_UUID, zero_text)
    assert rejected is None, "zero-vector insert attempt must be REJECTED"

    content_sha = hashlib.sha256(zero_text.encode("utf-8")).hexdigest()
    async with research_pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, false)", TEST_TENANT)
        row = await conn.fetchrow(
            """
            SELECT count(*) AS n FROM embedding_cache
            WHERE content_sha256 = $1 AND model_revision = $2
            """,
            content_sha,
            "qwen3-0.6b-latechunk-1024",
        )
    assert int(row["n"]) == 0, "rejected zero-vector must leave NO cache row"
