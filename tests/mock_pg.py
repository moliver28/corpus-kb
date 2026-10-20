"""Shared mock helpers for gateway/asyncpg fakes in tests.

``QueryHandler.handle_search`` runs its reads inside an explicit
``conn.transaction()`` block (tenant GUC + transaction-local HNSW settings,
U20/U40). AsyncMock auto-children make ``mock_conn.transaction()`` return a
COROUTINE, which breaks the ``async with`` — use :func:`transaction_cm` to
wire a working async context manager onto the fake connection.

:class:`FakeSqlGateway` (storage portability program, rule 19) is the shared
in-memory :class:`~corpus_kb.storage.gateway.SqlGateway` fake. Ported modules
adopt it instead of hand-rolled ``_ScriptedConn``-style fakes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock


def transaction_cm() -> MagicMock:
    """An async context manager standing in for asyncpg Connection.transaction()."""
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    return tx


def _default_status_tag(sql: str) -> str:
    """A PG-format tag synthesized from the statement's leading verb."""
    verb = sql.lstrip().split(" ", 1)[0].upper() if sql.strip() else ""
    return {
        "INSERT": "INSERT 0 1",
        "UPDATE": "UPDATE 1",
        "DELETE": "DELETE 1",
    }.get(verb, "OK")


class FakeSqlGateway:
    """In-memory SqlGateway fake for ported handler tests (program rule 19).

    Results are canned per SQL string. A key matches by exact equality first,
    then as a substring of the executed SQL (first match in insertion order).
    A ``list[dict]`` value feeds the fetch methods; a ``str`` value becomes
    the ``execute`` status tag. Unmatched SQL returns empty rows and a
    verb-derived default tag. Every call is recorded in ``calls`` as
    ``(sql, args)`` tuples, and transaction scopes bump ``commits`` /
    ``rollbacks`` so tests can pin lifecycle behavior.
    """

    def __init__(self, results: dict[str, Any] | None = None) -> None:
        self.results: dict[str, Any] = dict(results or {})
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.tenant_ids: list[str] = []
        self.tx_depth = 0
        self.commits = 0
        self.rollbacks = 0

    def _lookup(self, sql: str) -> Any:
        if sql in self.results:
            return self.results[sql]
        for key, value in self.results.items():
            if key in sql:
                return value
        return None

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, args))
        rows = self._lookup(sql)
        return [dict(row) for row in rows] if rows else []

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        row = await self.fetchrow(sql, *args)
        if not row:
            return None
        return next(iter(row.values()))

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        canned = self._lookup(sql)
        if isinstance(canned, str):
            return canned
        return _default_status_tag(sql)

    @asynccontextmanager
    async def transaction(self, tenant_id: str) -> AsyncIterator[FakeSqlGateway]:
        """Depth-counted scope: only the outermost exit commits or rolls back."""
        self.tenant_ids.append(tenant_id)
        self.tx_depth += 1
        try:
            yield self
        except BaseException:
            if self.tx_depth == 1:
                self.rollbacks += 1
            raise
        else:
            if self.tx_depth == 1:
                self.commits += 1
        finally:
            self.tx_depth -= 1
