"""The DuckDB SqlGateway driver (storage portability program, §3.1).

One ``DuckDBPyConnection`` per gateway. Every sync call runs in a thread
under a ``threading.Lock``; an ``asyncio.Lock`` is held across a whole
transaction scope, so concurrent scopes never interleave and outside
statements queue behind the open scope. Read calls ride a small pool of
duplicated cursors (``duckdb_read_pool.py``, issue #471); the pool sees
committed state only and never touches the write lock.

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
``duckdb_support.py``; the transaction vocabulary lives in
``duckdb_tx.py``.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import TYPE_CHECKING, Any, TypeVar, cast

from corpus_kb.storage.duckdb_params import bind_params, map_pg_casts
from corpus_kb.storage.duckdb_read_pool import (
    DEFAULT_READ_POOL_SIZE,
    POST_CLOSE_MESSAGE,
    DuckDbReadPool,
)
from corpus_kb.storage.duckdb_support import (
    _MAX_PLACEHOLDERS,
    Row,
    _check_append_only,
    _is_count_result,
    _reject_corpus_qualified,
    _Row,
    _status_tag,
    duckdb_conn_string_to_path,
    map_connect_error,
)
from corpus_kb.storage.duckdb_tx import DuckDbTxMixin

if TYPE_CHECKING:  # function-local at runtime (program rule 9: deptry-visible)
    import duckdb

T = TypeVar("T")


class DuckDbGateway(DuckDbTxMixin):
    """SqlGateway driver over one DuckDB file (or ``:memory:``)."""

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        *,
        read_pool: DuckDbReadPool[duckdb.DuckDBPyConnection] | None = None,
    ) -> None:
        self._conn = conn
        self.read_pool = read_pool
        self._closed = False
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
        read_pool_size: int = DEFAULT_READ_POOL_SIZE,
    ) -> DuckDbGateway:
        """Open the file with PG null placement; never hand duckdb a URL.

        ``enable_progress_bar`` is session-scope only (rejected at connect),
        so it is SET right after connect — it defaults ON without a tty.
        ``read_pool_size`` builds the issue #471 read-cursor pool; pass 0
        to disable it. ``:memory:`` databases never build a pool (a cursor
        there buys nothing).
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
        pool: DuckDbReadPool | None = None
        if read_pool_size > 0 and duckdb_conn_string_to_path(dsn) != ":memory:":
            pool = DuckDbReadPool(conn.cursor, read_pool_size)
        return cls(conn, read_pool=pool)

    async def close(self) -> None:
        self._closed = True
        if self.read_pool is not None:
            await self.read_pool.close()
        await self._run_sync(lambda conn: conn.close())

    def duplicate_connection(self) -> duckdb.DuckDBPyConnection:
        """A cursor on the SAME database instance (no second file lock).

        The graph store rides its own connection so its locks never
        interleave with gateway statements; ``cursor()`` attaches to the
        already-open database, so the one-writer posture stands (a second
        ``duckdb.connect`` on the file would fail the file lock).
        """
        return self._conn.cursor()

    async def fetch(self, sql: str, *args: Any) -> list[Row]:
        # The one guard choke point: fetch serves fetch/fetchrow/fetchval,
        # execute serves DML+DDL. Qualified SQL dies here, before any lock.
        # map_pg_casts + bind_params let Postgres-flavored statements and
        # uuid binds run unchanged (issue #330: the one translation site).
        _reject_corpus_qualified(sql)
        sql = map_pg_casts(sql)
        params = bind_params(args)
        if self._closed:
            # One unified post-close surface for both shapes (pool and
            # no-pool); see duckdb_read_pool.POST_CLOSE_MESSAGE.
            raise RuntimeError(POST_CLOSE_MESSAGE)
        if self.read_pool is not None and self._tx_owner is None:
            # Issue #471 contract: reads run in parallel ONLY while no
            # transaction is open anywhere. During a transaction, reads
            # queue behind the scope on the primary connection and observe
            # post-commit state; the scope owner keeps its read-back (W0).
            rows = await self.read_pool.run(lambda conn: self._rows(conn, sql, params))
        else:
            async with self._statement_gate():
                rows = await self._run_sync(lambda conn: self._rows(conn, sql, params))
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
        sql = map_pg_casts(sql)
        params = bind_params(args)
        async with self._statement_gate():
            return await self._run_sync(lambda conn: self._tag(conn, sql, params))

    def executemany(self, query: str, args: Sequence[Sequence[Any]]) -> Coroutine[Any, Any, None]:
        """Bulk write with execute's guards: one statement, many tuples."""
        _check_append_only(query)
        _reject_corpus_qualified(query)
        query = map_pg_casts(query)
        rows = [bind_params(row) for row in args]

        async def run() -> None:
            async with self._statement_gate():
                await self._run_sync(
                    lambda conn: conn.executemany(
                        query,
                        [tuple(row) for row in rows],
                    )
                )

        return run()

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
                params = bind_params(params)
                await self._run_sync(lambda conn, s=sql, p=params: conn.execute(s, p))

    # -- internals ---------------------------------------------------------

    async def _run_sync(self, fn: Callable[[duckdb.DuckDBPyConnection], T]) -> T:
        def _locked() -> T:
            with self._thread_lock:
                return fn(self._conn)

        return await asyncio.to_thread(_locked)

    def _rows(self, conn: duckdb.DuckDBPyConnection, sql: str, args: Sequence[Any]) -> list[_Row]:
        conn.execute(sql, list(args) or None)
        description = conn.description
        fetched = conn.fetchall()
        if description is None or _is_count_result(description):
            return []  # asyncpg parity: DML without RETURNING yields no rows
        names = [column[0] for column in description]
        return [_Row(zip(names, row, strict=True)) for row in fetched]

    def _tag(self, conn: duckdb.DuckDBPyConnection, sql: str, args: Sequence[Any]) -> str:
        conn.execute(sql, list(args) or None)
        return _status_tag(sql, conn)
