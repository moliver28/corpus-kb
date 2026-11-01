"""Child-process isolation for Docling window parses (issue #219).

Docling's scanner stack can crash the whole process on some scanned
pages. That crash is a hard stop no ``try``/``except`` can catch, so
each Docling window parses in its own spawned child. A crash or a
timeout kills only the child; the caller falls back to pypdf for that
one window and the run continues.

The child imports docling itself and ships plain page dicts back over a
queue, so the payload stays small and pickles without parser types.
Parent and child share one item-extraction rule
(``items_from_convert_result``), so an isolated window carries the same
items an in-process window would.

Tests patch :func:`_start_child` (the spawn seam), never ``multiprocessing``
itself, so no test starts a real child process.
"""

from __future__ import annotations

import importlib
import multiprocessing
import pickle
import time
from multiprocessing.process import BaseProcess
from pathlib import Path
from queue import Empty
from typing import Protocol

from .textbook_docling import (
    PageItem,
    items_from_convert_result,
    page_row,
    rich_fields_from_row,
)

#: One parsed page as a plain dict: ``page``, ``label``, ``text``, plus
#: the rich keys when the parse set them.
PageRow = dict[str, object]

#: The child-to-parent payload: one row per parsed page item.
WindowPayload = list[PageRow]

#: Poll cadence for the payload queue and the exit check (seconds).
_POLL_SECONDS = 0.2

#: Grace period for the child's exit after it ships its payload.
_REAP_SECONDS = 2.0

#: Seconds a terminated child gets before the parent kills it.
_KILL_GRACE_SECONDS = 5.0


class PayloadQueue(Protocol):
    """The slice of ``multiprocessing.Queue`` this module uses."""

    def get(self, block: bool = True, timeout: float | None = None) -> WindowPayload: ...

    def put(self, rows: WindowPayload, /) -> None: ...

    def close(self) -> None: ...


def _stop(proc: BaseProcess) -> None:
    """Terminate the child; kill it when it ignores the term signal.

    A child stuck inside native scanner code can ignore SIGTERM, so a
    plain join after terminate can hang forever on POSIX. The kill
    escalation bounds that wait.
    """
    proc.terminate()
    proc.join(_KILL_GRACE_SECONDS)
    if proc.is_alive():
        proc.kill()
    proc.join()


def _window_child(
    pdf_path: str, start: int, end: int, payload: PayloadQueue, structured: bool = False
) -> None:
    """Child target: import docling here, parse one window, ship dicts.

    The docling import stays inside the child; the parent only probes
    the module spec, so a full import never runs there. An import-time
    crash exits the child nonzero instead of taking the worker down. An
    uncaught error exits nonzero; the parent reads the exit code, never
    a traceback over the queue. ``structured`` turns on the rich item
    fields in the shipped rows.
    """
    module = importlib.import_module("docling.document_converter")
    converter = module.DocumentConverter()
    result = converter.convert(source=pdf_path, page_range=(start, end))
    payload.put([page_row(item) for item in items_from_convert_result(result, structured)])


def _start_child(
    pdf_path: str, start: int, end: int, structured: bool = False
) -> tuple[BaseProcess, PayloadQueue]:
    """Spawn the child and its payload queue (the test seam).

    ``spawn`` is required, not optional: the fork start method would
    inherit the parent's half-loaded parser state, which is exactly the
    state that crashes on scanned pages.
    """
    ctx = multiprocessing.get_context("spawn")
    payload: PayloadQueue = ctx.Queue()
    proc = ctx.Process(
        target=_window_child, args=(pdf_path, start, end, payload, structured), daemon=True
    )
    proc.start()
    return proc, payload


def parse_window_in_subprocess(
    pdf_path: Path, start: int, end: int, timeout_s: float, structured: bool = False
) -> list[PageItem] | None:
    """One window in a spawned child; ``None`` on crash, timeout, or no payload.

    The loop drains the queue and checks the exit code in short polls, so
    a large payload never blocks the join. Past the deadline the child is
    terminated and the caller falls back. A delivered payload is the
    success signal: a child that dies after shipping its rows still
    counts. A crash without a payload, a broken flush, or a timeout all
    return ``None``. ``structured`` passes through to the child.
    """
    proc, payload = _start_child(str(pdf_path), start, end, structured)
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
