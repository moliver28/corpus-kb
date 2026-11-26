"""F-03: the LlamaIndex startup probe is bounded and degrades (hermetic)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_initialize_probe_times_out_and_degrades(monkeypatch, caplog):
    import asyncio
    import time

    from corpus_kb.storage import llamaindex_backend as lib
    from corpus_kb.storage.llamaindex_backend import LlamaIndexDependencyError

    monkeypatch.setattr(lib, "LLAMAINDEX_PROBE_TIMEOUT_S", 0.05)

    class SlowEmbedding:
        async def aget_text_embedding(self, text):
            await asyncio.sleep(5.0)

    def no_extra(name):
        # The exact type the real _load raises when the extra is absent.
        raise LlamaIndexDependencyError(f"missing extra for {name}")

    monkeypatch.setattr(lib, "_load", no_extra)
    backend = lib.LlamaIndexPostgresBackend.__new__(lib.LlamaIndexPostgresBackend)
    backend._connection_string = "postgresql://u:p@localhost:5432/db"
    backend._ollama_embedding = SlowEmbedding()
    started = time.monotonic()
    with pytest.raises(LlamaIndexDependencyError):
        # initialize() survives the degraded probe, then hits the unguarded
        # _load("llama_index.vector_stores.postgres") — that call is the raiser.
        await backend.initialize()
    elapsed = time.monotonic() - started
    assert elapsed < 1.0  # pre-fix the probe sleeps the full 5 s inside this window
    assert "degraded" in caplog.text
