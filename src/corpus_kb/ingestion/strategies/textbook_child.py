"""The child side of the parser child seams: wire types and child targets.

Every spawned parser child (the per-window child of issue #219 and the
persistent worker of issue #270) runs a target in this module and
speaks these types. One module owns the child side, so both surfaces
call the same rows factory for their parser and a reused window
carries the same rows a fresh child would.

The wire payloads are plain dicts, so they pickle without parser
types. The parser imports stay inside the factories (the issue #220
rule): the parent only probes the module spec, and a parser import
crash exits the child nonzero instead of taking the parent down.
"""

from __future__ import annotations

import importlib
import traceback
from collections.abc import Callable
from typing import Protocol

from corpus_kb.errors import ValidationError

from .textbook_docling import items_from_convert_result, page_row

#: One parsed page as a plain dict: ``page``, ``label``, ``text``, plus
#: the rich keys when the parse set them.
PageRow = dict[str, object]

#: The child-to-parent payload: one row per parsed page item.
WindowPayload = list[PageRow]

#: One parent-to-worker request: path, 1-based inclusive range, flag;
#: ``None`` tells the worker to stop.
WorkerRequest = tuple[str, int, int, bool] | None


class PayloadQueue(Protocol):
    """The child-to-parent slice of ``multiprocessing.Queue``.

    ``put`` also takes ``None``: the worker ships it for a window that
    raised, so the parent falls back while the worker stays alive.
    """

    def get(self, block: bool = True, timeout: float | None = None) -> WindowPayload: ...

    def put(self, rows: WindowPayload | None, /) -> None: ...

    def close(self) -> None: ...


class RequestQueue(Protocol):
    """The parent-to-worker slice of ``multiprocessing.Queue``."""

    def get(self, block: bool = True, timeout: float | None = None) -> WorkerRequest: ...

    def put(self, request: WorkerRequest, /) -> None: ...

    def close(self) -> None: ...


def _docling_window_rows(
    pdf_path: str, start: int, end: int, structured: bool = False
) -> list[dict[str, object]]:
    """One docling window as rows; the docling import stays in the child."""
    module = importlib.import_module("docling.document_converter")
    converter = module.DocumentConverter()
    result = converter.convert(source=pdf_path, page_range=(start, end))
    return [page_row(item) for item in items_from_convert_result(result, structured)]


def _paddle_window_rows(
    pdf_path: str, start: int, end: int, structured: bool = False
) -> list[dict[str, object]]:
    """One paddle-vl window as rows (the paddle module owns the parse).

    A sibling import, not a module-level one: this module owns the
    wrapper, the paddle module owns the parse, and each imports the
    other only inside its own edge.
    """
    from .textbook_paddle import paddle_window_rows

    return paddle_window_rows(pdf_path, start, end, structured)


#: One rows factory per registry parser that parses in a child. The
#: invariant ``set(_WINDOW_ROW_FACTORIES) == set(_PARSER_SEAMS)`` is
#: pinned in the tests, so a fourth registry parser cannot silently
#: fall into another parser's child.
_WINDOW_ROW_FACTORIES: dict[str, Callable[[str, int, int, bool], list[dict[str, object]]]] = {
    "docling": _docling_window_rows,
    "paddle-vl": _paddle_window_rows,
}


def _window_child(
    pdf_path: str,
    parser: str,
    start: int,
    end: int,
    payload: PayloadQueue,
    structured: bool = False,
) -> None:
    """Per-window child target: import the parser, parse, ship, exit.

    An uncaught error exits nonzero; the parent reads the exit code,
    never a traceback over the queue. ``structured`` turns on the rich
    item fields in the shipped rows (a no-op for paddle-vl today). An
    unknown parser raises: no parser ever borrows another's rows.
    """
    factory = _WINDOW_ROW_FACTORIES.get(parser)
    if factory is None:
        raise ValidationError(f"no child rows factory for parser {parser!r}")
    payload.put(factory(pdf_path, start, end, structured))


def _worker_main(parser: str, requests: RequestQueue, payload: PayloadQueue) -> None:
    """Persistent worker target: load the parser once, serve until stopped.

    A window that raises ships ``None`` rows: the parent hands that
    window to pypdf and the loaded worker stays alive. A crash inside
    native parser code kills the process; the parent sees the exit
    code and spawns a fresh worker for the next window.
    """
    factory = _WINDOW_ROW_FACTORIES.get(parser)
    if factory is None:
        raise ValidationError(f"no child rows factory for parser {parser!r}")
    while True:
        request = requests.get()
        if request is None:
            return
        pdf_path, start, end, structured = request
        try:
            payload.put(factory(pdf_path, start, end, structured))
        except Exception:
            traceback.print_exc()  # the child's stderr reaches the run's log
            payload.put(None)
