"""F-15: pool settings come from one constants module."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_http_pool_reads_connect_constants(monkeypatch):
    from corpus_kb import server_wiring as sw
    from corpus_kb.storage import connect as cn

    captured = {}

    class FakePool:
        pass

    async def fake_create_pool(dsn, **kwargs):
        captured.update(kwargs)
        return FakePool()

    monkeypatch.setattr("asyncpg.create_pool", fake_create_pool)
    monkeypatch.setattr(cn, "POOL_MAX_SIZE", 7)
    pool = await sw.initialize_postgres_pool("postgresql://u:p@h:5432/db")
    assert isinstance(pool, FakePool)
    assert captured["max_size"] == 7  # before the fix: literal 20 — the drift the finding names
    assert captured["min_size"] == cn.POOL_MIN_SIZE
    assert captured["command_timeout"] == cn.POOL_COMMAND_TIMEOUT_S
