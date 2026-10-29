"""Worker support pieces (textbook-ingestion program, Wave 2C).

The lease identity, the cooperative stop flag and its signal wiring, the
backend guard, the config-knob readers, and the heartbeat scope. They sit
apart from the drain loop to keep both files under the size limit.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid as uuid_mod
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from corpus_kb.errors import ConfigError
from corpus_kb.utils.config_sections import mapping_section

if TYPE_CHECKING:
    from .store import IngestQueueStore

__all__ = [
    "StopFlag",
    "WorkerIdentity",
    "install_stop_signals",
    "positive_float",
    "positive_int",
    "require_postgres_backend",
]


@dataclass(frozen=True)
class WorkerIdentity:
    """The lease identity written to ``claimed_by``: host, pid, random token."""

    host: str
    pid: int
    token: str

    @classmethod
    def for_this_process(cls) -> WorkerIdentity:
        return cls(host=socket.gethostname(), pid=os.getpid(), token=uuid_mod.uuid4().hex[:12])

    @property
    def claimed_by(self) -> str:
        return f"{self.host}:{self.pid}:{self.token}"


class StopFlag:
    """One cooperative stop switch (the signal path sets it; tests may too)."""

    def __init__(self) -> None:
        self._requested = False
        self.reason: str | None = None

    def request(self, reason: str = "stop requested") -> None:
        if not self._requested:
            self.reason = reason
        self._requested = True

    @property
    def requested(self) -> bool:
        return self._requested

    def __bool__(self) -> bool:
        return self._requested


def require_postgres_backend(config: dict[str, object]) -> str:
    """The database backend, typed-refused when the worker cannot drain it.

    The queue store rides the asyncpg pool (Wave 1 seam). DuckDB is a
    sole-writer engine and its queue port has not landed, so a duckdb
    selection stops here instead of failing deep inside the pool.
    """
    database = mapping_section(config, "database")
    backend = str(database.get("backend") or "postgres")
    if backend == "postgres":
        return backend
    if backend == "duckdb":
        raise ConfigError(
            "The queue worker needs the Postgres backend. DuckDB allows one writer.",
            remediation="Set database.backend to postgres. Then run the worker again.",
        )
    raise ConfigError(
        f"The database backend must be postgres or duckdb, not {backend}.",
        remediation="Set database.backend to postgres. Then run the worker again.",
    )


def install_stop_signals(flag: StopFlag) -> None:
    """Map SIGINT/SIGTERM onto the stop flag (main thread, Windows-safe).

    The first signal finishes the current book and ends the pass. A
    second SIGINT restores the default handler, so Ctrl-C can always
    force the process down.
    """
    import signal

    def _on_signal(signum: int, frame: object) -> None:
        if signum == signal.SIGINT and flag.requested:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            raise KeyboardInterrupt
        flag.request(f"signal {signum}")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except ValueError:
            # Not the main thread (tests, embedded loops): no signal path.
            return


def positive_float(raw: object, default: float) -> float:
    """The knob as a float above zero; junk answers the default."""
    try:
        value = float(raw) if isinstance(raw, (int, float)) else float(str(raw))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def positive_int(raw: object, default: int) -> int:
    """The knob as an int of one or more; junk answers the default."""
    if isinstance(raw, bool) or raw is None:
        return default
    try:
        value = int(str(raw))
    except ValueError:
        return default
    return value if value >= 1 else default


@asynccontextmanager
async def heartbeat(
    store: IngestQueueStore,
    queue_id: str,
    claimed_by: str,
    interval: float,
    on_lost: Callable[[], None],
) -> AsyncIterator[None]:
    """Keep one claim alive while its book runs; flag a lost claim."""

    async def _beat() -> None:
        while True:
            await asyncio.sleep(interval)
            if not await store.heartbeat(queue_id, claimed_by):
                on_lost()
                return

    beat = asyncio.create_task(_beat())
    try:
        yield
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat
