"""Worker support pieces (textbook-ingestion program, Wave 2C + 3).

The lease identity, the cooperative stop flag and its signal wiring, the
backend guard, the config-knob readers, the heartbeat scope, and the
Wave 3 per-book helpers: the row's profile and strategy settings ride
into the pipeline through ONE config copy, and the claim-time page probe
fills ``pages_total`` best-effort. They sit apart from the drain loop to
keep both files under the size limit.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid as uuid_mod
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from corpus_kb.errors import ConfigError
from corpus_kb.utils.config_sections import mapping_section

if TYPE_CHECKING:
    from .store import IngestQueueStore

__all__ = [
    "StopFlag",
    "WorkerIdentity",
    "book_config_for",
    "fill_pages_total",
    "install_stop_signals",
    "page_count_for",
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


# --- Wave 3: per-book config and the claim-time page probe ------------------


def _strategy_overrides(raw: object) -> dict[str, object]:
    """The row's strategy settings as a dict, or {} when absent or unreadable.

    The enqueuer validated a JSON object, and asyncpg hands jsonb back as
    text, so the text parses here. Junk answers {} — queue metadata never
    fails a book.
    """
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def book_config_for(config: dict[str, object], row: Mapping[str, object]) -> dict[str, object]:
    """One per-book config copy with the row's profile, title, and strategy.

    The row's ``profile`` sets ``ingestion.profile`` — the one key
    ``run_pipeline`` already resolves through ``load_profile``. The row's
    ``title`` sets ``ingestion.title``; the pipeline puts it on the result
    envelope, and the Ingested event lifts it into documents.title
    (issue #222). The row's ``strategy_config`` merges into the
    ``textbook:`` block, the one settings block ``settings_from`` reads;
    unknown keys are inert there. The input config is never mutated, and
    blank values leave the copy untouched.
    """
    book = dict(config)
    ingestion: dict[str, object] | None = None
    profile = row.get("profile")
    if isinstance(profile, str) and profile.strip():
        ingestion = dict(mapping_section(config, "ingestion"))
        ingestion["profile"] = profile.strip()
    title = row.get("title")
    if isinstance(title, str) and title.strip():
        if ingestion is None:
            ingestion = dict(mapping_section(config, "ingestion"))
        ingestion["title"] = title.strip()
    if ingestion is not None:
        book["ingestion"] = ingestion
    overrides = _strategy_overrides(row.get("strategy_config"))
    if overrides:
        textbook = dict(mapping_section(config, "textbook"))
        textbook.update(overrides)
        book["textbook"] = textbook
    return book


def page_count_for(path: str) -> int | None:
    """Best-effort pypdf page count for queue-status; None on any failure.

    The probe rides the textbook strategy's ``open_pdf`` seam (pypdf
    imports lazily). A missing extra, a missing file, or a corrupt file
    all answer None: the page total is metadata, never a claim risk.
    """
    from corpus_kb.ingestion.strategies.textbook_pages import open_pdf

    try:
        reader, note = open_pdf(Path(path))
        if reader is None or note is not None:
            return None
        return len(reader.pages)
    except Exception:
        return None


async def fill_pages_total(
    store: IngestQueueStore, row: Mapping[str, object], claimed_by: str
) -> None:
    """Write the probe's page count onto one live claim, best-effort.

    No probe answer (or a claim lost in between) leaves the column NULL.
    """
    total = page_count_for(str(row.get("path") or ""))
    if total is not None:
        await store.set_pages_total(str(row.get("id")), claimed_by, total)
