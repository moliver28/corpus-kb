"""The event-loop home for the persistent embedding cache (issue #539).

asyncpg connections bind to the loop that created them. The cache runs
inside worker threads (``embed_batch`` reaches the cache through
``asyncio.to_thread``), so a fresh ``asyncio.run`` per call would touch the
caller's shared pool from a different loop and corrupt its connections.
One daemon loop thread owns every cache call instead:

- Coroutines ride ``run_coroutine_threadsafe`` onto that loop.
- A Postgres cache gets its own small pool, created ON the cache loop, so
  the caller's shared pool is never crossed with a foreign loop.
- A DuckDB gateway keeps its passed-in object: its one connection is
  thread-locked by design.

The thread is a daemon and lives for the process: the cache is best
effort and shuts down with the process. Every call carries a wall-clock
bound, so a wedged store degrades to a miss instead of hanging ingest
(the nli_client lesson).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any

from corpus_kb.storage.gateway import PostgresGateway, SqlGateway

logger = logging.getLogger(__name__)

#: Wall-clock bound per cache call (seconds). A store slower than this
#: counts as a miss; the caller keeps embedding without the cache.
CACHE_CALL_TIMEOUT_SECONDS = 120.0

#: Dedicated pool shape for the Postgres cache arm: tiny, lazy, and used
#: only by this module. The shared pool keeps serving the handlers.
_CACHE_POOL_MIN = 0
_CACHE_POOL_MAX = 2

_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_LOCK = threading.Lock()


def cache_loop() -> asyncio.AbstractEventLoop:
    """Return the one long-lived loop that runs every cache coroutine."""
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None or _LOOP.is_closed():
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, name="embedding-cache", daemon=True).start()
        return _LOOP


class CacheRuntime:
    """Run one cache coroutine on the cache loop with an honest bound.

    ``gateway`` is the caller-provided seam. When it wraps a shared
    asyncpg pool (``PostgresGateway``), this runtime builds its own pool
    from the configured DSN so the shared pool is never touched from the
    cache loop. Any other gateway (DuckDB driver, test double) runs as
    given: the DuckDB driver is thread-locked, and test doubles carry no
    live connections.
    """

    def __init__(self, gateway: SqlGateway) -> None:
        self._gateway = gateway
        self._needs_own_pool = isinstance(gateway, PostgresGateway)
        self._own: SqlGateway | None = None
        self._own_lock = threading.Lock()
        self._own_error: str | None = None

    def run(self, make_coro: Callable[[SqlGateway], Coroutine[Any, Any, Any]]) -> Any:
        """Run one cache coroutine to completion; raise on any failure.

        The coroutine receives the prepared gateway, so a Postgres cache
        never touches the caller's shared pool. The coroutine object is
        built only after the pool is ready, so a skipped or failed
        preparation never leaks an un-awaited coroutine.
        """
        loop = cache_loop()
        gateway = self._ready_gateway(loop)
        future = asyncio.run_coroutine_threadsafe(make_coro(gateway), loop)
        return future.result(CACHE_CALL_TIMEOUT_SECONDS)

    def _ready_gateway(self, loop: asyncio.AbstractEventLoop) -> SqlGateway:
        """Return the gateway this call must use, prepared for the loop."""
        if not self._needs_own_pool:
            return self._gateway
        with self._own_lock:
            if self._own is not None or self._own_error is not None:
                if self._own is None:
                    raise RuntimeError(self._own_error or "cache pool unavailable")
                return self._own
            try:
                self._own = asyncio.run_coroutine_threadsafe(
                    self._build_own_gateway(), loop
                ).result(CACHE_CALL_TIMEOUT_SECONDS)
            except Exception as exc:  # best effort: no pool, no cache
                self._own_error = str(exc)
                logger.warning(
                    "embedding_cache_2560 disabled: its own pool failed to open: %s", exc
                )
                raise RuntimeError(str(exc)) from exc
            return self._own

    async def _build_own_gateway(self) -> SqlGateway:
        """Open the dedicated pool on the cache loop and wrap it.

        The DSN comes from the config the rest of the process uses, so
        the cache lands in the same database the handlers serve.
        """
        pool = await _create_pool(
            dsn=_cache_dsn(),
            min_size=_CACHE_POOL_MIN,
            max_size=_CACHE_POOL_MAX,
            command_timeout=CACHE_CALL_TIMEOUT_SECONDS,
        )
        return PostgresGateway(pool)


async def _create_pool(dsn: str, min_size: int, max_size: int, command_timeout: float) -> Any:
    """Open one asyncpg pool (module-level for test substitution)."""
    import asyncpg

    return await asyncpg.create_pool(
        dsn, min_size=min_size, max_size=max_size, command_timeout=command_timeout
    )


def _cache_dsn() -> str:
    """The effective database DSN (module-level for test substitution)."""
    from corpus_kb.config import load_config
    from corpus_kb.storage.connect import config_conn_str

    return config_conn_str(load_config())
