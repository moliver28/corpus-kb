"""Transaction machinery for the DuckDB gateway (issue #471 split).

The gateway keeps ONE connection with a lock pair. This mixin owns the
transaction vocabulary so ``duckdb_gateway.py`` stays under the 250-line
soft limit; the public surface is unchanged (``transaction()`` and
``run_transaction()`` ride the same gateway instance).

DuckDB semantics encoded here (W0-verified, findings/storage-ops.md):

- Parser and catalog errors leave a transaction usable, so a nested
  ``transaction()`` is a no-op and the scope keeps working.
- Conversion errors ABORT the transaction. The gateway surfaces the error
  faithfully; rollback and replay is the caller's concern.
- Write-write conflicts raise ``TransactionException`` with "Conflict" at
  UPDATE time and abort the unit. ``run_transaction`` replays the whole
  BEGIN..COMMIT unit, at most ``MAX_TRANSACTION_ATTEMPTS`` attempts. Only
  the concrete ``TransactionException`` type is matched — never a broad
  ``OperationalError``, which also catches locked-file ``IOException``.

Attribute contract (declared below, initialized by
``DuckDbGateway.__init__``): ``_conn``, ``_thread_lock``, ``_tx_lock``,
``_tx_owner`` plus the ``_run_sync`` helper.
"""

from __future__ import annotations

import asyncio
import random
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any, TypeVar, cast

from corpus_kb.storage.duckdb_support import _BACKOFF_SECONDS, MAX_TRANSACTION_ATTEMPTS

if TYPE_CHECKING:  # function-local at runtime (program rule 9: deptry-visible)
    import duckdb

    from corpus_kb.storage.duckdb_gateway import DuckDbGateway

T = TypeVar("T")
_RunT = TypeVar("_RunT")

#: Attribute-contract type for the gateway's thread hand-off helper.
RunSyncFn = Callable[[Callable[[duckdb.DuckDBPyConnection], _RunT]], Awaitable[_RunT]]


class DuckDbTxMixin:
    """BEGIN/COMMIT scope, nesting no-op, and conflict replay."""

    # Attribute contract with DuckDbGateway (set in its __init__).
    _conn: duckdb.DuckDBPyConnection
    _thread_lock: threading.Lock
    _tx_lock: asyncio.Lock
    _tx_owner: asyncio.Task[Any] | None
    _run_sync: RunSyncFn

    def transaction(self, tenant_id: str) -> AbstractAsyncContextManager[DuckDbGateway]:
        return cast("AbstractAsyncContextManager[DuckDbGateway]", self._transaction_scope())

    async def run_transaction(
        self, tenant_id: str, body: Callable[[DuckDbGateway], Awaitable[T]]
    ) -> T:
        """Run ``body`` in a transaction, replaying the whole unit on conflict."""
        import duckdb

        conflict: Exception | None = None
        for attempt in range(MAX_TRANSACTION_ATTEMPTS):
            try:
                async with self.transaction(tenant_id):
                    return await body(cast("DuckDbGateway", self))
            except duckdb.TransactionException as exc:
                if "Conflict" not in str(exc):
                    raise
                conflict = exc
                if attempt + 1 < MAX_TRANSACTION_ATTEMPTS:
                    await asyncio.sleep(
                        _BACKOFF_SECONDS * 2**attempt * random.SystemRandom().uniform(0.8, 1.2)
                    )
        assert conflict is not None
        raise conflict

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[DuckDbGateway]:
        if asyncio.current_task() is self._tx_owner:
            # Nested scope: depth-counter no-op — DuckDB has no SAVEPOINT and
            # parser/catalog failures leave the transaction usable, so the
            # fail-open degraded-probe contracts port unchanged.
            yield cast("DuckDbGateway", self)
            return
        async with self._tx_lock:
            self._tx_owner = asyncio.current_task()
            try:
                await self._run_sync(lambda conn: conn.execute("BEGIN TRANSACTION"))
                try:
                    yield cast("DuckDbGateway", self)
                except BaseException:
                    with suppress(Exception):
                        await self._run_sync(lambda conn: conn.execute("ROLLBACK"))
                    raise
                await self._run_sync(lambda conn: conn.execute("COMMIT"))
            finally:
                self._tx_owner = None

    @asynccontextmanager
    async def _statement_gate(self) -> AsyncIterator[None]:
        """Owner-task statements ride the open scope; others wait on the lock."""
        if asyncio.current_task() is self._tx_owner:
            yield
            return
        async with self._tx_lock:
            yield
