"""F-12: the absent provider label resolves to the default, not "pgml"."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_missing_provider_resolves_ollama_and_skips_second_embedder(monkeypatch):
    from types import SimpleNamespace

    from corpus_kb.tools.ingest_embed import _embed_with_fallback

    built = []

    class ZeroEmbedder:
        # SYNC on purpose: aembed_batch runs non-pgml embedders in a worker
        # thread, so a sync embed_batch mirrors the real OllamaEmbedder.
        def embed_batch(self, texts):
            return [[0.0] * 4 for _ in texts]

    def stub_factory(config, pool):
        built.append(dict((config or {}).get("embedding", {})))
        return ZeroEmbedder()

    monkeypatch.setattr("corpus_kb.tools.ingest_embed.create_embedder", stub_factory)
    chunks = [SimpleNamespace(chunk_id="c1", embedding=None)]
    degraded, note = await _embed_with_fallback(
        {"embedding": {"fallback_provider": "ollama"}}, None, None, chunks, ["t"]
    )
    assert len(built) == 1  # before the fix: 2 — the fallback embedder is built
    assert degraded is True
    assert note is not None
    assert "pgml" not in note  # before the fix the note names the wrong provider
    assert "zero vectors" in note
