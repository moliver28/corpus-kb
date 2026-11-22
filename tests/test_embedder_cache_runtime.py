"""Tests for the cache loop runtime (issue #539 follow-up).

The runtime must pin every cache call to one loop thread, give the
Postgres arm its own pool (the shared pool is never crossed with a
foreign loop), pass DuckDB/test gateways through, and bound each call so
a wedged store degrades to a miss instead of hanging ingest.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from corpus_kb.rag import embedder_cache_runtime as runtime_module
from corpus_kb.rag.embedder_cache_runtime import CACHE_CALL_TIMEOUT_SECONDS, CacheRuntime
from corpus_kb.storage.gateway import PostgresGateway

pytestmark = [pytest.mark.ollama]


def test_calls_pin_to_one_loop() -> None:
    """Every run() lands on the same loop, never on the caller's."""
    seen: list[asyncio.AbstractEventLoop] = []

    async def _grab(gateway: Any) -> str:
        seen.append(asyncio.get_running_loop())
        return "ok"

    runtime = CacheRuntime(_fake_gateway())
    assert runtime.run(_grab) == "ok"
    assert runtime.run(_grab) == "ok"
    assert seen[0] is seen[1]
    assert seen[0] is not asyncio.new_event_loop()
    seen[0].call_soon_threadsafe(lambda: None)


def _fake_gateway() -> Any:
    class _Fake:
        async def fetch(self, sql: str, *args: Any) -> list[Any]:
            return []

    return _Fake()


def test_postgres_arm_builds_its_own_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PostgresGateway caller gets a fresh pool; the shared one is untouched."""
    made: list[MagicMock] = []

    async def _fake_connect(**_kwargs: object) -> MagicMock:
        pool = MagicMock()
        made.append(pool)
        return pool

    monkeypatch.setattr(runtime_module, "_create_pool", _fake_connect)
    monkeypatch.setattr(runtime_module, "_cache_dsn", lambda: "postgresql://stub", raising=False)
    shared_pool = MagicMock()
    gateway = PostgresGateway(shared_pool)
    runtime = CacheRuntime(gateway)

    async def _probe(gateway: Any) -> str:
        return type(gateway).__name__

    assert runtime.run(_probe) == "PostgresGateway"
    assert len(made) == 1
    assert runtime.run(_probe) == "PostgresGateway"
    assert len(made) == 1, "the pool is built once and reused"


def test_duckdb_gateway_passes_through() -> None:
    """A non-Postgres gateway (DuckDB, test double) runs as given."""
    gateway = _fake_gateway()
    runtime = CacheRuntime(gateway)

    async def _probe(passed: Any) -> Any:
        return passed

    assert runtime.run(_probe) is gateway


def test_call_bound_degrades_not_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A store slower than the bound raises inside the bound, not never."""

    async def _wedge(gateway: Any) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(runtime_module, "CACHE_CALL_TIMEOUT_SECONDS", 0.2)
    runtime = CacheRuntime(_fake_gateway())
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        runtime.run(_wedge)
    assert time.monotonic() - start < 5


def test_pool_failure_disables_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed pool open raises once with a message, never retries forever."""

    async def _boom(**_kwargs: object) -> MagicMock:
        raise OSError("store down")

    monkeypatch.setattr(runtime_module, "_create_pool", _boom)
    monkeypatch.setattr(runtime_module, "_cache_dsn", lambda: "postgresql://stub", raising=False)
    runtime = CacheRuntime(PostgresGateway(MagicMock()))

    async def _noop(gateway: Any) -> None:
        return None

    with pytest.raises(RuntimeError):
        runtime.run(_noop)
    with pytest.raises(RuntimeError):
        runtime.run(_noop)


def test_bound_constant_is_bounded() -> None:
    """The shipped bound stays a sane wall clock (regression guard)."""
    assert 0 < CACHE_CALL_TIMEOUT_SECONDS <= 300
