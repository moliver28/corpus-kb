"""F-09: the AGE startup probe falls back on any Postgres error."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_age_probe_postgres_error_falls_back(monkeypatch, caplog):
    import asyncpg

    from corpus_kb.storage import graph_factory as gf

    class ExplodingAge:
        def __init__(self, pool):
            pass

        async def search_entities(self, query):
            raise asyncpg.PostgresError("permission denied for function ag_catalog")

    class FallbackStore:
        def __init__(self, pool):
            pass

    monkeypatch.setattr("corpus_kb.storage.AgeGraphStore", ExplodingAge)
    monkeypatch.setattr("corpus_kb.storage.PostgresGraphStore", FallbackStore)
    cfg = {"database": {"backend": "postgres"}, "graph": {"backend": "age"}}
    store = await gf.create_graph_store(cfg, source=object(), backend="postgres")
    assert isinstance(store, FallbackStore)
    assert "Falling back" in caplog.text
