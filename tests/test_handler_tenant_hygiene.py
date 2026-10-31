"""Tag and versioning handlers: tenant scope and narrow error catches.

Two pins live here. Tenant scope: every storage method runs inside
``storage.tenant_conn.tenant_connection`` (acquire, transaction, and
set_config together), so multi-statement writes commit as one
transaction. Catches: a missing ``event_store`` table keeps the
graceful empty result; every other error propagates. The pre-fix
catches swallowed all exceptions, so real faults hid as empty pages.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import asyncpg
import duckdb
import pytest

from corpus_kb.handlers.tag_handler import TagHandler
from corpus_kb.handlers.versioning_handler import VersioningHandler

_TENANT = uuid4()


class _FakeConn:
    """Pool-path connection stub. Raises the injected error on event_store."""

    def __init__(self) -> None:
        self.error: Exception | None = None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[_FakeConn]:
        yield self

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_FakeConn]:
        yield self

    async def execute(self, sql: str, *args: object) -> str:
        return "SET"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self._probe(sql)
        return []

    async def fetchval(self, sql: str, *args: object) -> int:
        self._probe(sql)
        return 0

    def _probe(self, sql: str) -> None:
        if self.error is not None and "event_store" in sql:
            raise self.error


class _FakePool:
    """Pool stub: has acquire, so is_gateway keeps it on the pool arm."""

    def __init__(self) -> None:
        self.conn = _FakeConn()
        self.probe = self.conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_FakeConn]:
        yield self.conn


class _FakeScope:
    """Gateway transaction scope stub with the same probe rule."""

    def __init__(self) -> None:
        self.error: Exception | None = None

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self._probe(sql)
        return []

    async def fetchval(self, sql: str, *args: object) -> int:
        self._probe(sql)
        return 0

    async def execute(self, sql: str, *args: object) -> str:
        return "INSERT 0 1"

    def _probe(self, sql: str) -> None:
        if self.error is not None and "event_store" in sql:
            raise self.error


class _FakeGateway:
    """Gateway stub: no acquire, so is_gateway takes the gateway arm."""

    def __init__(self) -> None:
        self.scope = _FakeScope()
        self.probe = self.scope

    @asynccontextmanager
    async def transaction(self, tenant_id: str) -> AsyncIterator[_FakeScope]:
        yield self.scope


def _inject(
    monkeypatch: pytest.MonkeyPatch, fake: _FakePool | _FakeGateway, error: Exception
) -> None:
    monkeypatch.setattr(fake.probe, "error", error)


_FAKE_KINDS = [_FakePool, _FakeGateway]

_MISSING_TABLE_ERRORS = [
    pytest.param(_FakePool, asyncpg.UndefinedTableError("relation does not exist"), id="pool-pg"),
    pytest.param(
        _FakeGateway, asyncpg.UndefinedTableError("relation does not exist"), id="gateway-pg"
    ),
    pytest.param(
        _FakeGateway,
        duckdb.CatalogException("Table event_store does not exist"),
        id="gateway-duckdb",
    ),
]


@pytest.mark.parametrize("fake_cls,error", _MISSING_TABLE_ERRORS)
async def test_list_versions_missing_table_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
    fake_cls: type[_FakePool] | type[_FakeGateway],
    error: Exception,
) -> None:
    fake = fake_cls()
    _inject(monkeypatch, fake, error)
    assert await VersioningHandler(fake).handle_list_versions(_TENANT) == []


@pytest.mark.parametrize("fake_cls,error", _MISSING_TABLE_ERRORS)
async def test_get_stats_missing_table_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    fake_cls: type[_FakePool] | type[_FakeGateway],
    error: Exception,
) -> None:
    fake = fake_cls()
    _inject(monkeypatch, fake, error)
    stats = await VersioningHandler(fake).handle_get_stats(_TENANT)
    assert stats["total_events"] == 0
    assert "documents" in stats


@pytest.mark.parametrize("fake_cls", _FAKE_KINDS)
async def test_list_versions_other_error_propagates(
    monkeypatch: pytest.MonkeyPatch, fake_cls: type[_FakePool] | type[_FakeGateway]
) -> None:
    fake = fake_cls()
    _inject(monkeypatch, fake, RuntimeError("pool down"))
    with pytest.raises(RuntimeError, match="pool down"):
        await VersioningHandler(fake).handle_list_versions(_TENANT)


@pytest.mark.parametrize("fake_cls", _FAKE_KINDS)
async def test_get_stats_other_error_propagates(
    monkeypatch: pytest.MonkeyPatch, fake_cls: type[_FakePool] | type[_FakeGateway]
) -> None:
    fake = fake_cls()
    _inject(monkeypatch, fake, RuntimeError("pool down"))
    with pytest.raises(RuntimeError, match="pool down"):
        await VersioningHandler(fake).handle_get_stats(_TENANT)


def test_every_tag_and_versioning_method_reads_inside_tenant_connection() -> None:
    """Structural: each storage method uses the shared tenant helper."""
    for cls in (TagHandler, VersioningHandler):
        for name, member in vars(cls).items():
            if not name.startswith("handle_") or not callable(member):
                continue
            source = inspect.getsource(member)
            label = f"{cls.__name__}.{name}"
            assert "tenant_connection(" in source, f"{label} bypasses tenant_connection"
            assert "pool.acquire()" not in source, f"{label} acquires outside the tenant scope"
