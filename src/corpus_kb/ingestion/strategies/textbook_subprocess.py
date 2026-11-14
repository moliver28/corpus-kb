"""Child-process isolation for heavy parser window parses (issue #219).

A scanner stack can crash the whole process on some scanned pages.
That crash is a hard stop no ``try``/``except`` can catch, so each
heavy window (docling, paddle-vl) parses in its own spawned child. A
crash or a timeout kills only the child; the caller falls back to pypdf
for that one window and the run continues.

Two child surfaces share this module's parent side. The default
persistent worker (:mod:`textbook_worker`, issue #270) loads the
parser once and serves window after window. With reuse off, this
module's own spawn-per-window path runs instead: one child per window,
the exact isolation issue #219 shipped. Both surfaces speak the plain
page rows of :mod:`textbook_child`, so the payload stays small and
pickles without parser types, and both share one item-extraction rule
per parser with the in-process path.

Tests patch :func:`_start_child` (the spawn seam of the per-window
path), never ``multiprocessing`` itself, so no test starts a real
child process.
"""

from __future__ import annotations

import multiprocessing
import pickle
import time
from multiprocessing.process import BaseProcess
from pathlib import Path
from queue import Empty

from .textbook_child import (
    PayloadQueue,
    WindowPayload,
    _window_child,
)
from .textbook_docling import PageItem
from .textbook_settings import WorkerPolicy
from .textbook_worker import (
    _POLL_SECONDS,
    _REAP_SECONDS,
    _items_from_rows,
    _stop,
    parse_window_with_worker,
)


def _start_child(
    pdf_path: str, parser: str, start: int, end: int, structured: bool = False
) -> tuple[BaseProcess, PayloadQueue]:
    """Spawn the child and its payload queue (the test seam).

    ``spawn`` is required, not optional: the fork start method would
    inherit the parent's half-loaded parser state, which is exactly the
    state that crashes on scanned pages.
    """
    ctx = multiprocessing.get_context("spawn")
    payload: PayloadQueue = ctx.Queue()
    proc = ctx.Process(
        target=_window_child,
        args=(pdf_path, parser, start, end, payload, structured),
        daemon=True,
    )
    proc.start()
    return proc, payload


def parse_window_in_subprocess(
    pdf_path: Path,
    parser: str,
    start: int,
    end: int,
    timeout_s: float,
    structured: bool = False,
    worker: WorkerPolicy | None = None,
) -> list[PageItem] | None:
    """One window in a spawned child; ``None`` on crash, timeout, or no payload.

    A ``worker`` policy with ``reuse`` on routes the window to the
    persistent worker (:func:`parse_window_with_worker`), which keeps
    one loaded child across windows (issue #270). Without it, this
    path spawns one child for this window only. The loop drains the
    queue and checks the exit code in short polls, so a large payload
    never blocks the join. Past the deadline the child is terminated
    and the caller falls back. A delivered payload is the success
    signal: a child that dies after shipping its rows still counts. A
    crash without a payload, a broken flush, or a timeout all return
    ``None``. ``parser`` picks the child target; ``structured`` passes
    through to the child.
    """
    if worker is not None and worker.reuse:
        return parse_window_with_worker(
            pdf_path,
            parser,
            start,
            end,
            timeout_s,
            structured,
            worker.max_windows,
            worker.idle_ttl_s,
        )
    proc, payload = _start_child(str(pdf_path), parser, start, end, structured)
    deadline = time.monotonic() + timeout_s
    rows: WindowPayload | None = None
    while True:
        try:
            rows = payload.get(timeout=_POLL_SECONDS)
            break
        except (Empty, EOFError, OSError, pickle.UnpicklingError):
            rows = None
        if proc.exitcode is not None:
            # The child exited. Its queue flush lands before exit, but the
            # parent may not have drained the pipe yet: one short re-read.
            try:
                rows = payload.get(timeout=_POLL_SECONDS)
            except (Empty, EOFError, OSError, pickle.UnpicklingError):
                rows = None
            break
        if time.monotonic() >= deadline:
            _stop(proc)
            payload.close()
            return None
        proc.join(timeout=_POLL_SECONDS)
    proc.join(timeout=_REAP_SECONDS)
    if proc.is_alive():
        # The payload is out or the child is gone; stop a slow exit here.
        _stop(proc)
    payload.close()
    if rows is None:
        return None
    return _items_from_rows(rows)
