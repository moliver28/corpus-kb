"""The persistent parser worker: one loaded parser across windows (#270).

Spawn-per-window isolation (issue #219) pays the parser's model load in
every window child. That load dominates the Docling CPU path (the W3-A
probe: 357 s per 100 pages), so the parent now keeps one worker child
alive across windows and documents and sends each window to it as one
request. The model loads once per worker, not once per window.

The worker speaks the same plain page rows the per-window child ships
(:mod:`textbook_child` owns the child side), so a reused window
carries the same items a fresh child would. Each worker's life is
bounded: the parent recycles it after ``max_windows`` served windows,
drops it after a crash or a missed window deadline, and the idle
reaper drops it once it has sat idle past ``idle_ttl_s`` (checked at
the next acquire), so a long-lived server sheds idle parser children
instead of holding them for its whole life. A lost window still falls
back to pypdf for that window only; the next window spawns a fresh
worker.

Every parse runs single-flight under the parser's lock, so two
threads can never share one worker or steal another window's rows
from its payload queue — even if a caller moves strategy parsing off
the event loop onto worker threads.

Tests patch :func:`_start_worker` (the spawn seam) and swap the
``_WORKERS`` table, so no test starts a real child process.
"""

from __future__ import annotations

import multiprocessing
import pickle
import threading
import time
from dataclasses import dataclass, field
from multiprocessing.process import BaseProcess
from pathlib import Path
from queue import Empty

from .textbook_child import (
    PayloadQueue,
    RequestQueue,
    WindowPayload,
    _worker_main,
)
from .textbook_docling import PageItem, rich_fields_from_row
from .textbook_settings import DEFAULT_WORKER_IDLE_TTL_S, DEFAULT_WORKER_MAX_WINDOWS

#: Poll cadence for the payload queue and the exit check (seconds).
_POLL_SECONDS = 0.2

#: Grace period for a child's exit after it ships its payload (seconds).
_REAP_SECONDS = 2.0

#: Seconds a terminated child gets before the parent kills it.
_KILL_GRACE_SECONDS = 5.0


def _stop(proc: BaseProcess) -> None:
    """Terminate the child; kill it when it ignores the term signal.

    A child stuck inside native parser code can ignore SIGTERM, so a
    plain join after terminate can hang forever on POSIX. The kill
    escalation bounds that wait.
    """
    proc.terminate()
    proc.join(_KILL_GRACE_SECONDS)
    if proc.is_alive():
        proc.kill()
    proc.join()


def _items_from_rows(rows: WindowPayload) -> list[PageItem]:
    """PageItems from the child's dicts; malformed rows are skipped.

    A malformed rich value marks the whole row bad, the same shape rule
    the window cache applies.
    """
    items: list[PageItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue  # a corrupt payload row skips, never fails the window
        page = row.get("page")
        label = row.get("label")
        text = row.get("text")
        if isinstance(page, bool) or not isinstance(page, int):
            continue
        if not isinstance(label, str) or not isinstance(text, str):
            continue
        rich = rich_fields_from_row(row)
        if rich is None:
            continue
        items.append(
            PageItem(
                page,
                label,
                text,
                level=rich.level,
                table_markdown=rich.table_markdown,
                bbox=rich.bbox,
            )
        )
    return items


@dataclass
class _WorkerHandle:
    """One live worker child with its queues and its served-window count.

    ``last_served`` is the monotonic timestamp of the last serve (it
    starts at spawn); the idle reaper reads it at the next acquire.
    """

    parser: str
    proc: BaseProcess
    requests: RequestQueue
    payload: PayloadQueue
    windows_served: int = 0
    last_served: float = field(default_factory=time.monotonic)


#: The live workers, one per parser name at most (bounded, not per window).
#:
#: INVARIANT (single-flight): all access to a worker — acquire, request
#: put, payload read, serve-count update — happens inside that parser's
#: parse lock, so no caller can read another window's rows from a shared
#: payload queue. This holds even if a caller moves strategy parsing off
#: the event loop onto worker threads.
_WORKERS: dict[str, _WorkerHandle] = {}

#: One parse lock per parser name; a parse holds it for the whole
#: window exchange. The worker pool is small, so the guard-locked
#: create-on-first-use below is enough.
_PARSE_LOCKS: dict[str, threading.Lock] = {}

#: Guards the lock table itself (two threads may name the same parser).
_PARSE_LOCKS_GUARD = threading.Lock()


def _parser_lock(parser: str) -> threading.Lock:
    """The parse lock for ``parser``, created on first use."""
    with _PARSE_LOCKS_GUARD:
        lock = _PARSE_LOCKS.get(parser)
        if lock is None:
            lock = threading.Lock()
            _PARSE_LOCKS[parser] = lock
        return lock


def _start_worker(parser: str) -> _WorkerHandle:
    """Spawn the worker child and its queues (the test seam).

    ``spawn`` is required, not optional: the fork start method would
    inherit the parent's half-loaded parser state, which is exactly the
    state that crashes on scanned pages.
    """
    ctx = multiprocessing.get_context("spawn")
    requests: RequestQueue = ctx.Queue()
    payload: PayloadQueue = ctx.Queue()
    proc = ctx.Process(target=_worker_main, args=(parser, requests, payload), daemon=True)
    proc.start()
    return _WorkerHandle(parser=parser, proc=proc, requests=requests, payload=payload)


def _discard_worker(parser: str, handle: _WorkerHandle) -> None:
    """Stop one worker and drop it from the table (a recycle or a loss)."""
    if _WORKERS.get(parser) is handle:
        del _WORKERS[parser]
    handle.requests.close()
    handle.payload.close()
    _stop(handle.proc)


def _acquire_worker(parser: str, max_windows: int, idle_ttl_s: float) -> _WorkerHandle:
    """The live worker for ``parser``: kept, recycled, reaped, or spawned.

    A worker past ``max_windows`` served windows, dead from a crash, or
    idle past ``idle_ttl_s`` is stopped and replaced, so no worker
    grows without bound across documents and no idle server holds a
    loaded parser child forever.
    """
    handle = _WORKERS.get(parser)
    if handle is not None:
        stale = handle.windows_served >= max_windows
        idle = time.monotonic() - handle.last_served >= idle_ttl_s
        if stale or idle or handle.proc.exitcode is not None:
            _discard_worker(parser, handle)
        else:
            return handle
    handle = _start_worker(parser)
    _WORKERS[parser] = handle
    return handle


def _collect_response(handle: _WorkerHandle, deadline: float) -> tuple[bool, WindowPayload | None]:
    """Poll for one window response; ``(False, None)`` is a crash or timeout.

    ``None`` rows with ``True`` are a worker-reported window failure: the
    payload arrived, so the worker served the request and stays alive.
    """
    while True:
        try:
            return True, handle.payload.get(timeout=_POLL_SECONDS)
        except (Empty, EOFError, OSError, pickle.UnpicklingError):
            pass
        if handle.proc.exitcode is not None:
            # The worker died mid-window. Its queue flush lands before
            # exit, but the parent may not have drained the pipe yet:
            # one short re-read.
            try:
                return True, handle.payload.get(timeout=_POLL_SECONDS)
            except (Empty, EOFError, OSError, pickle.UnpicklingError):
                return False, None
        if time.monotonic() >= deadline:
            return False, None
        handle.proc.join(timeout=_POLL_SECONDS)


def parse_window_with_worker(
    pdf_path: Path,
    parser: str,
    start: int,
    end: int,
    timeout_s: float,
    structured: bool = False,
    max_windows: int = DEFAULT_WORKER_MAX_WINDOWS,
    idle_ttl_s: float = DEFAULT_WORKER_IDLE_TTL_S,
) -> list[PageItem] | None:
    """One window through the persistent worker; ``None`` hands it to pypdf.

    The worker for ``parser`` is spawned on first use and kept across
    windows and documents, so the parser's model load is paid once per
    worker life. A crashed, timed-out, idle-past-``idle_ttl_s``, or
    worker-reported-failed worker is dropped; the caller falls back to
    pypdf for that window only, and the next window finds or spawns a
    healthy worker. The whole exchange runs under the parser's parse
    lock, so concurrent callers queue instead of sharing the worker.
    """
    with _parser_lock(parser):
        handle = _acquire_worker(parser, max_windows, idle_ttl_s)
        handle.requests.put((str(pdf_path), start, end, structured))
        delivered, rows = _collect_response(handle, time.monotonic() + timeout_s)
        if not delivered:
            _discard_worker(parser, handle)  # a dead or stuck worker never serves again
            return None
        handle.windows_served += 1
        handle.last_served = time.monotonic()
        if rows is None:
            return None
        return _items_from_rows(rows)
