"""Docling window seams for the textbook strategy: confirm, parse, fall back.

The strategy orchestrates windows; these two helpers own the docling
edges, so ``textbook.py`` stays the thin orchestrator.

``confirm_docling`` is the one-time extra check per book. Subprocess
mode probes the module spec only — the child imports docling, and a
parent import can crash the worker (issue #220); in-process mode loads
the converter. A missing extra warns once and the book degrades to
pypdf.

``parse_one_docling_window`` runs one window: in a spawned child when
``window_subprocess`` is set, else one converter call. A crashed,
timed-out, or wrapper-failed window takes the pypdf path (issue #219).
Both seams append their warnings to the caller's window list, so each
window's notes travel with its outcome.
"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path
from typing import Any

from .textbook_pages import (
    PageItem,
    docling_env_warnings,
    load_docling_converter,
    parse_window_docling,
)
from .textbook_settings import TextbookSettings
from .textbook_subprocess import parse_window_in_subprocess

_MISSING_DOCLING = "textbook strategy needs the optional 'docling' extra; used pypdf page text"


def confirm_docling(
    settings: TextbookSettings,
    converter: Any | None,
    warnings: list[str],
) -> tuple[bool, Any | None]:
    """The one-time extra check: (docling active, converter or None).

    Subprocess mode probes the module spec only: the child imports
    docling, and a parent import can crash the worker (issue #220).
    """
    if settings.window_subprocess:
        active = find_spec("docling") is not None
    else:
        converter = load_docling_converter()
        active = converter is not None
    if not active:
        warnings.append(_MISSING_DOCLING)
    else:
        warnings.extend(docling_env_warnings())
    return active, converter


def parse_one_docling_window(
    settings: TextbookSettings,
    converter: Any | None,
    path: Path,
    start: int,
    end: int,
    index: int,
    warnings: list[str],
) -> list[PageItem] | None:
    """One docling window; ``None`` hands the window to the pypdf fallback.

    ``settings.structured_output`` passes through to the parse, so a
    flag-on window carries the rich item fields end to end.
    """
    if settings.window_subprocess:
        try:
            items = parse_window_in_subprocess(
                path,
                "docling",
                start,
                end,
                settings.window_timeout_s,
                settings.structured_output,
            )
        except Exception:
            items = None  # a wrapper failure must not kill the run
        if items is None:
            warnings.append(
                f"Window {index + 1} crashed or timed out in the child process. "
                "Used the pypdf fallback."
            )
        return items
    try:
        return parse_window_docling(converter, path, start, end, settings.structured_output)
    except Exception:
        warnings.append(f"docling failed on pages {start} to {end}; used pypdf page text")
        return None
