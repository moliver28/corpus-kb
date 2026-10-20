"""The DuckDB SqlGateway driver (storage portability program, §3.1).

One ``DuckDBPyConnection`` per gateway. Every sync call runs in a thread
under a ``threading.Lock``; an ``asyncio.Lock`` is held across a whole
transaction scope, so concurrent scopes never interleave and outside
statements queue behind the open scope.

DuckDB semantics encoded here (W0-verified, findings/storage-ops.md):

- Parser and catalog errors leave a transaction usable, so a nested
  ``transaction()`` is a no-op and the scope keeps working.
- Conversion errors ABORT the transaction. The gateway surfaces the error
  faithfully; rollback and replay is the caller's concern.
- Write-write conflicts raise ``TransactionException`` with "Conflict" at
  UPDATE time and abort the unit. :meth:`DuckDbGateway.run_transaction`
  replays the whole BEGIN..COMMIT unit, at most 5 attempts. Only the
  concrete ``TransactionException`` type is matched — never a broad
  ``OperationalError``, which also catches locked-file ``IOException``.

Shaping helpers (DSN conversion, status tags, append-only guard,
``corpus.``-qualification guard, connect error mapping) live in
``duckdb_support.py``.
"""

from __future__ import annotations

import asyncio
import random
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any, TypeVar, cast

from corpus_kb.storage.duckdb_support import (
    _BACKOFF_SECONDS,
    _MAX_PLACEHOLDERS,
    MAX_TRANSACTION_ATTEMPTS,
    Row,
    _check_append_only,
    _is_count_result,
    _reject_corpus_qualified,
    _Row,
    _status_tag,
    duckdb_conn_string_to_path,
    map_connect_error,
)

if TYPE_CHECKING:  # function-local at runtime (program rule 9: deptry-visible)
    import duckdb

T = TypeVar("T")


class DuckDbGateway:
    """SqlGateway driver over one DuckDB file (or ``:memory:``)."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self._thread_lock = threading.Lock()
        self._tx_lock = asyncio.Lock()
        self._tx_owner: asyncio.Task[Any] | None = None

    @classmethod
    async def connect(
        cls,
        dsn: str,
        *,
        config: Mapping[str, Any] | None = None,
        read_only: bool = False,
    ) -> DuckDbGateway:
        """Open the file with PG null placement; never hand duckdb a URL.

        ``enable_progress_bar`` is session-scope only (rejected at connect),
        so it is SET right after connect — it defaults ON without a tty.
        """
        import duckdb

        connect_config: dict[str, Any] = {"default_null_order": "postgres"}
        if config:
            connect_config.update(config)
        try:
            conn = duckdb.connect(
                duckdb_conn_string_to_path(dsn), read_only=read_only, config=connect_config
            )
        except Exception as exc:
            raise map_connect_error(exc) from exc
        conn.execute("SET enable_progress_bar = false")
        return cls(conn)

    async def close(self) -> None:
        await self._run_sync(lambda conn: conn.close())

    async def fetch(self, sql: str, *args: Any) -> list[Row]:
        # The one guard choke point: fetch serves fetch/fetchrow/fetchval,
        # execute serves DML+DDL. Qualified SQL dies here, before any lock.
        _reject_corpus_qualified(sql)
        async with self._statement_gate():
            rows = await self._run_sync(lambda conn: self._rows(conn, sql, args))
        return cast("list[Row]", rows)

    async def fetchrow(self, sql: str, *args: Any) -> Row | None:
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        rows = await self.fetch(sql, *args)
        return next(iter(rows[0].values())) if rows else None

    async def execute(self, sql: str, *args: Any) -> str:
        _check_append_only(sql)
        _reject_corpus_qualified(sql)
        async with self._statement_gate():
            return await self._run_sync(lambda conn: self._tag(conn, sql, args))

    async def write_batch(
        self, table: str, rows: Sequence[Mapping[str, Any]], *, chunk_rows: int | None = None
    ) -> None:
        """Insert many rows via chunked multi-row VALUES.

        One bound parameter per value: pass an array column as one Python
        list, never per element (W0 gotcha).
        """
        if not rows:
            return
        columns = list(rows[0])
        per_chunk = (
            chunk_rows if chunk_rows is not None else max(1, _MAX_PLACEHOLDERS // len(columns))
        )
        row_sql = "(" + ", ".join("?" for _ in columns) + ")"
        head = f"INSERT INTO {table} ({', '.join(columns)}) VALUES "
        async with self._statement_gate():
            for start in range(0, len(rows), per_chunk):
                chunk = rows[start : start + per_chunk]
                sql = head + ", ".join([row_sql] * len(chunk))
                params = [row[column] for row in chunk for column in columns]
                await self._run_sync(lambda conn, s=sql, p=params: conn.execute(s, p))

    def transaction(self, tenant_id: str) -> AbstractAsyncContextManager[DuckDbGateway]:
        return self._transaction_scope()

    async def run_transaction(
        self, tenant_id: str, body: Callable[[DuckDbGateway], Awaitable[T]]
    ) -> T:
        """Run ``body`` in a transaction, replaying the whole unit on conflict."""
        import duckdb

        conflict: Exception | None = None
        for attempt in range(MAX_TRANSACTION_ATTEMPTS):
            try:
                async with self.transaction(tenant_id):
                    return await body(self)
            except duckdb.TransactionException as exc:
                if "Conflict" not in str(exc):
                    raise
                conflict = exc
                if attempt + 1 < MAX_TRANSACTION_ATTEMPTS:
                    await asyncio.sleep(_BACKOFF_SECONDS * 2**attempt * random.uniform(0.8, 1.2))
        assert conflict is not None
        raise conflict

    # -- internals ---------------------------------------------------------

    @asynccontextmanager
    async def _transaction_scope(self) -> AsyncIterator[DuckDbGateway]:
        if asyncio.current_task() is self._tx_owner:
            # Nested scope: depth-counter no-op — DuckDB has no SAVEPOINT and
            # parser/catalog failures leave the transaction usable, so the
            # fail-open degraded-probe contracts port unchanged.
            yield self
            return
        async with self._tx_lock:
            self._tx_owner = asyncio.current_task()
            try:
                await self._run_sync(lambda conn: conn.execute("BEGIN TRANSACTION"))
                try:
                    yield self
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

    async def _run_sync(self, fn: Callable[[duckdb.DuckDBPyConnection], T]) -> T:
        def _locked() -> T:
            with self._thread_lock:
                return fn(self._conn)

        return await asyncio.to_thread(_locked)

    def _rows(self, conn: duckdb.DuckDBPyConnection, sql: str, args: tuple[Any, ...]) -> list[_Row]:
        conn.execute(sql, list(args) or None)
        description = conn.description
        fetched = conn.fetchall()
        if description is None or _is_count_result(description):
            return []  # asyncpg parity: DML without RETURNING yields no rows
        names = [column[0] for column in description]
        return [_Row(zip(names, row, strict=True)) for row in fetched]

    def _tag(self, conn: duckdb.DuckDBPyConnection, sql: str, args: tuple[Any, ...]) -> str:
        conn.execute(sql, list(args) or None)
        return _status_tag(sql, conn)
