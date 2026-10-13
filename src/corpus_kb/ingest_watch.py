"""Watch mode for the ``corpus-kb ingest`` front door (Stage 5, v1 local loop).

Polls a directory (recursively — the same ``rglob`` + suffix walk the one-shot
directory ingest uses, so watched subtrees are never silently unmonitored),
ingests every supported file through the caller's ``ingest_file`` callback
(the same pipeline a one-shot ingest uses), and AUTO-HEALS exactly one failure
class: runs whose error text marks the embedding provider as unavailable
(``EmbeddingError: Connection failed``). Anything else — parse errors,
Postgres write failures — is NEVER retried silently; it stays failed in the
summary.

Per-file crash isolation: an unexpected exception out of ``ingest_file``
(e.g. a transient database blip) is logged, counted in the summary's
``failed`` count, and the loop continues — one bad file never kills the
watcher. ``asyncio.CancelledError`` still propagates (cancellation is not a
file failure). Failures at watcher STARTUP (pool open, ledger probe — before
the loop) are NOT isolated: they propagate to the CLI wrapper, which maps
``asyncpg.PostgresError`` to exit 3, same as one-shot mode.

Ledger: every run lands in the ``ingest_runs`` table (migration 022) via
``run_pipeline`` itself — the watcher does not duplicate it. When the table
is absent the caller warns visibly and the watcher keeps its bookkeeping in
memory only (``ledger.persistent=False``); heal attempt counts are
watcher-side state either way (no schema for them exists, by design — this
is a v1 local loop).

Testability: the sleeper, the health probe, and the ingest callback are
injectable; ``WatchOptions.max_passes`` bounds the loop instead of running
forever. Ctrl-C is handled by the CLI wrapper (KeyboardInterrupt -> exit 0).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

import asyncpg

# The pipeline writes stage errors as "EmbeddingError: <embed_err>" and
# embed_chunks prefixes every provider outage with "Connection failed"
# (including the all-zero-marker case). Requiring BOTH substrings keeps the
# heal set tight: only embedder outages qualify, never parse or write errors.
HEALABLE_MARKERS = ("EmbeddingError", "Connection failed")

# The front-door modality table (v1: text/code/markdown; transcript formats
# arrive via --source-type strategies). ONE source of truth: the one-shot
# directory ingest (tools/ingest_tools.ingest_directory) walks this set too.
WATCH_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".lua",
    ".md",
    ".markdown",
    ".rst",
    ".txt",
}

# Cap on the exponential heal backoff so a long outage does not sleep days.
MAX_BACKOFF_MULTIPLIER = 64

_FAILED_STATUSES = ("error", "partial")

# Where the watcher narrates (print for the CLI, an appending list method
# for tests). A plain callable alias: ``print`` itself is a valid Reporter.
Reporter = Callable[[str], None]


def is_embedding_unavailable_error(error: str | None) -> bool:
    """True when an ingest error text marks the embedding provider as down."""
    if not error:
        return False
    return all(marker in error for marker in HEALABLE_MARKERS)


def is_healable_result(result: dict[str, object]) -> bool:
    """True when a pipeline result failed ONLY because embeddings were down.

    Covers both shapes: a ``partial`` run whose stage errors carry the
    embedder-outage text, and an ``error`` result whose message does.
    """
    status = str(result.get("status", ""))
    if status == "success":
        return False
    if status == "partial":
        errors = result.get("errors")
        error_text = "; ".join(str(e) for e in errors) if isinstance(errors, list) else ""
        return bool(error_text) and is_embedding_unavailable_error(error_text)
    message = result.get("message") or result.get("error")
    return is_embedding_unavailable_error(str(message) if message else None)


@dataclass
class WatchOptions:
    """Tunables for the watch loop (all bounded; nothing runs unbounded)."""

    interval_s: float = 10.0
    heal: bool = True
    heal_max_attempts: int = 5
    heal_backoff_s: float = 15.0
    max_passes: int | None = None  # None = run until cancelled (Ctrl-C)


@dataclass
class _PathState:
    """Watcher-side bookkeeping for one path (attempt counts, last outcome).

    ``attempts == -1`` means settled: either ingested successfully or
    abandoned after the heal cap — never armed again this session.
    """

    attempts: int = 0
    status: str = "pending"
    error: str | None = None


@dataclass
class WatchLedger:
    """Run bookkeeping: persistent (ingest_runs) or in-memory fallback."""

    paths: dict[str, _PathState] = field(default_factory=dict)
    persistent: bool = True


def scan_watch_dir(directory: Path) -> list[Path]:
    """One directory pass: supported files only, sorted for stable logs.

    Recursive parity with the one-shot ingest (``cli_ingest._resolve_files``):
    the same ``rglob`` walk, the same ``WATCH_SUFFIXES`` filter, no extra
    ignore patterns — watch and one-shot agree on what counts as an
    ingestable file, including nested subtrees.
    """
    if directory.is_file():
        return [directory]
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in WATCH_SUFFIXES
    )


async def default_health_probe(config: dict[str, object], pool: asyncpg.Pool | None) -> bool:
    """Probe the CONFIGURED embedding chain end-to-end.

    Real vectors from the configured provider (or its fallback chain) are
    the only honest "provider is back" signal — a TCP check would miss a
    running-but-broken model. All-zero output means still degraded.
    """
    from .rag import aembed_batch, create_embedder

    try:
        vectors = await aembed_batch(create_embedder(config, pool), ["ingest watch health probe"])
    except Exception:
        return False
    return bool(vectors) and any(any(v != 0.0 for v in vector) for vector in vectors)


async def ledger_available(pool: asyncpg.Pool, tenant_id: str) -> bool:
    """True when the ``ingest_runs`` ledger exists (to_regclass guard).

    Runs the probe inside a tenant-GUC transaction: ``FORCE RLS`` makes
    GUC-less reads return zero rows silently, so the GUC is set on the same
    connection before probing (the doctor/db-checks pattern).
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.current_tenant_id', $1, true)", tenant_id)
        return await conn.fetchval("SELECT to_regclass('public.ingest_runs')") is not None


async def watch_directory(
    directory: Path,
    ingest_file: Callable[[Path], Awaitable[dict[str, object]]],
    options: WatchOptions,
    *,
    probe: Callable[[], Awaitable[bool]] | None = None,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
    clock: Callable[[], float] = monotonic,
    report: Reporter = print,
    ledger: WatchLedger | None = None,
) -> dict[str, object]:
    """Poll ``directory`` forever (or ``max_passes`` times), healing embed outages.

    ``ingest_file`` is the SAME callback a one-shot ingest uses, so watched
    behavior is never a second pipeline. Returns an honest summary when the
    loop ends (max_passes reached or the task cancelled).
    """
    probe_fn = probe if probe is not None else _true_probe
    sleep_fn = sleeper if sleeper is not None else _real_sleeper
    book = ledger if ledger is not None else WatchLedger()
    passes = 0
    ingested = 0
    failed = 0
    healed = 0
    started = clock()
    while options.max_passes is None or passes < options.max_passes:
        passes += 1
        for path in scan_watch_dir(directory):
            result, _crash = await _ingest_isolated(ingest_file, path, report)
            _record(book, str(path), result)
            if str(result.get("status", "")) == "success":
                ingested += 1
            else:
                # Every non-success counts as failed — pipeline-reported
                # errors and crashes alike — so `failed` never reads 0
                # beside a non-empty broken set.
                failed += 1
                if options.heal and is_healable_result(result):
                    report(f"watch: {path} failed with embedding unavailable; auto-heal armed")
        if options.heal:
            heal_healed, heal_crashed = await _heal_pass(
                book, options, ingest_file, probe_fn, sleep_fn, report
            )
            healed += heal_healed
            failed += heal_crashed
        if options.max_passes is not None and passes >= options.max_passes:
            break
        await sleep_fn(options.interval_s)
    still_broken = sorted(
        key for key, state in book.paths.items() if state.status in _FAILED_STATUSES
    )
    return {
        "status": "success" if not still_broken else "partial",
        "passes": passes,
        "ingested": ingested,
        "failed": failed,
        "healed": healed,
        "still_broken": still_broken,
        "ledger_persistent": book.persistent,
        "elapsed_s": round(clock() - started, 3),
    }


async def _ingest_isolated(
    ingest_file: Callable[[Path], Awaitable[dict[str, object]]],
    path: Path,
    report: Reporter,
) -> tuple[dict[str, object], str | None]:
    """One per-file ingest with crash isolation.

    Returns ``(result, crash_text)``. An unexpected exception out of the
    pipeline is logged (path + exception), converted into an honest ``error``
    result so the bookkeeping still sees the path, and surfaced as a non-None
    crash text for the summary's ``failed`` count — the watcher continues with
    the next file/pass. ``asyncio.CancelledError`` is NOT caught: shutting the
    watcher down is not a file failure. Startup failures (pool open, ledger
    probe) never reach here, so a PostgresError there still exits 3 like
    one-shot mode.
    """
    try:
        return await ingest_file(path), None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        report(f"watch: {path} failed unexpectedly ({text}); continuing")
        return {"status": "error", "path": str(path), "error": text}, text


async def _heal_pass(
    book: WatchLedger,
    options: WatchOptions,
    ingest_file: Callable[[Path], Awaitable[dict[str, object]]],
    probe: Callable[[], Awaitable[bool]],
    sleeper: Callable[[float], Awaitable[None]],
    report: Reporter,
) -> tuple[int, int]:
    """One auto-heal attempt over every armed path (exponential probe backoff).

    A path leaves the heal set only on a successful re-ingest. When the
    attempt cap is hit the remaining paths are abandoned LOUDLY — never
    retried silently, never dropped quietly. Returns ``(healed, crashed)``:
    a re-ingest that raises is isolated exactly like the scan pass (logged,
    counted, path stays failed) — no separate heal logic for raised errors.
    """
    armed = {
        key: state
        for key, state in book.paths.items()
        if state.attempts >= 0
        and state.status in _FAILED_STATUSES
        and is_embedding_unavailable_error(state.error)
    }
    if not armed:
        return 0, 0
    attempt = max(state.attempts for state in armed.values())
    if attempt >= options.heal_max_attempts:
        for key in armed:
            book.paths[key].attempts = -1
            report(f"watch: auto-heal gave up on {key} after {attempt} attempts (still broken)")
        return 0, 0
    backoff = min(
        options.heal_backoff_s * (2**attempt),
        options.heal_backoff_s * MAX_BACKOFF_MULTIPLIER,
    )
    await sleeper(backoff)
    if not await probe():
        for state in armed.values():
            state.attempts += 1
        report(f"watch: embedding provider still down (heal probe failed, attempt {attempt + 1})")
        return 0, 0
    healed = 0
    crashed = 0
    for key in sorted(armed):
        result, crash = await _ingest_isolated(ingest_file, Path(key), report)
        _record(book, key, result)
        if str(result.get("status", "")) == "success":
            healed += 1
            report(f"watch: healed {key}")
        else:
            if crash is not None:
                crashed += 1
            book.paths[key].attempts = attempt + 1
    return healed, crashed


def _record(book: WatchLedger, key: str, result: dict[str, object]) -> None:
    state = book.paths.setdefault(key, _PathState())
    state.status = str(result.get("status", "error"))
    state.error = _error_text(result)
    if state.status == "success" or state.attempts < 0:
        state.attempts = -1
    elif is_healable_result(result):
        state.attempts = max(state.attempts, 0)


def _error_text(result: dict[str, object]) -> str | None:
    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        return "; ".join(str(e) for e in errors)
    message = result.get("message") or result.get("error")
    return str(message) if message else None


async def _true_probe() -> bool:
    return True


async def _real_sleeper(seconds: float) -> None:
    await asyncio.sleep(seconds)
