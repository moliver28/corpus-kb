"""The tenant advisory write lock (projections/_locks.py), hermetic.

The real Postgres race (two concurrent relations delete+insert units under
READ COMMITTED) needs a live server; these pins cover the helper's OWN
contract: first-try take, bounded contention wait, typed exhaustion, and
the best-effort unlock on a failed body. The DuckDB no-op shim pair is
the real dialect, so the engine split rides the same calls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from corpus_kb.errors import ConflictError
from corpus_kb.projections._locks import tenant_write_lock
from corpus_kb.storage.dialect import DUCKDB

TENANT = "00000000-0000-0000-0000-000000000001"
KEY = "documents-projection:relations"


class _StubScope:
    """SqlGateway stand-in: scripted fetchval results, recorded SQL."""

    def __init__(self, results: list[Any]) -> None:
        self._results = list(results)
        self.sql: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.sql.append(sql.split("(")[0])
        return self._results.pop(0) if self._results else True

    def transaction(self, tenant_id: str) -> AsyncIterator[Any]:
        @asynccontextmanager
        async def _scope() -> AsyncIterator[_StubScope]:
            yield self

        return _scope()


async def test_lock_taken_first_try_unlocks_after_the_body() -> None:
    scope = _StubScope([True, True])
    async with tenant_write_lock(scope, DUCKDB, TENANT, KEY):
        assert scope.sql == ["SELECT length"]  # the no-op try-lock ran
    assert len(scope.sql) == 2  # the unlock ran after the body


async def test_lock_waits_through_contention_then_runs_the_body() -> None:
    scope = _StubScope([False, False, True, True])
    async with tenant_write_lock(scope, DUCKDB, TENANT, KEY):
        pass
    # The no-op dialect's take and unlock share one SQL shape, so the pin
    # is the call budget: two refusals, the take, then the unlock.
    assert len(scope.sql) == 4


async def test_lock_exhaustion_fails_typed_without_unlock() -> None:
    scope = _StubScope([False] * 100)
    with pytest.raises(ConflictError):
        async with tenant_write_lock(scope, DUCKDB, TENANT, KEY):
            pass
    assert len(scope.sql) == 100  # refusals only — nothing was taken, no unlock


async def test_failed_body_still_unlocks_and_reraises() -> None:
    scope = _StubScope([True, True])
    with pytest.raises(ValueError, match="body failed"):
        async with tenant_write_lock(scope, DUCKDB, TENANT, KEY):
            raise ValueError("body failed")
    assert len(scope.sql) == 2  # the best-effort unlock ran
