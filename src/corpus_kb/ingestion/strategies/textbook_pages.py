"""PDF probing and window parsing for the textbook strategy.

One window — a 1-based inclusive page span — parses at a time, so one
book never monopolizes a run. Two parsers share one record shape:

* ``docling`` (optional ``[docling]`` extra, the primary parser): each
  window is one ``DocumentConverter.convert`` call with a 1-based
  inclusive ``page_range`` tuple. Known limit (docling discussion #2744):
  a windowed parse loses global heading and TOC context; this module only
  records per-page items with their labels and page numbers.
* ``pypdf`` (optional ``[scan]`` extra, the degrade parser): plain
  page-window text, no labels. Bookmarks come from pypdf in both modes.

Both extras import lazily (the semchunk/code pattern); CI installs
neither, so these edges stay invisible to static resolution. Tests
substitute :func:`load_docling_converter` with a fake converter.

pypdf (through fontTools) floods the log with font warnings on some
books. :func:`hush_pdf_logs` raises those logger families to ERROR while
a parse runs and restores them after.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Version tags baked into cache records; a parser change invalidates the
#: records the old version wrote (the cache key carries this tag).
PARSER_VERSIONS: dict[str, str] = {"docling": "docling-v1", "pypdf": "pypdf-v1"}

_NOISY_LOGGERS = ("pypdf", "fontTools")


@dataclass(frozen=True)
class PageItem:
    """One parsed block: its 1-based page, its parser label, its text."""

    page: int
    label: str
    text: str


@dataclass(frozen=True)
class OutlineBookmark:
    """One flattened bookmark: title, 1-based anchor page, nesting depth."""

    title: str
    page: int
    level: int


@dataclass(frozen=True)
class WindowRecord:
    """One parsed window: the page span and its per-page items."""

    start_page: int
    end_page: int
    items: list[PageItem]


@contextmanager
def hush_pdf_logs() -> Iterator[None]:
    """Raise the noisy PDF parser loggers to ERROR, then restore them.

    A level gate, not a logging.Filter: a child logger inherits the level
    of its nearest named ancestor, so one setLevel call covers the whole
    ``pypdf`` and ``fontTools`` families. A Filter on an ancestor logger
    never sees the records its children log.
    """
    saved: list[tuple[logging.Logger, int]] = []
    for name in _NOISY_LOGGERS:
        gate = logging.getLogger(name)
        saved.append((gate, gate.level))
        gate.setLevel(logging.ERROR)
    try:
        yield
    finally:
        for gate, level in saved:
            gate.setLevel(level)


def _load_pypdf() -> Any | None:
    """The lazy ``[scan]`` import seam (CI never installs the extra)."""
    try:
        return importlib.import_module("pypdf")
    except ImportError:
        return None


def open_pdf(path: Path) -> tuple[Any | None, str | None]:
    """Open ``path`` with pypdf: ``(reader, note)`` — reader None on failure.

    The note says which failure hit: a missing ``[scan]`` extra or a file
    that does not open as a PDF. The caller turns the note into a warning.
    """
    pypdf = _load_pypdf()
    if pypdf is None:
        return None, "the optional 'scan' extra is missing (pip install 'corpus-kb[scan]')"
    try:
        with hush_pdf_logs():
            return pypdf.PdfReader(str(path)), None
    except Exception as exc:
        logger.info("pypdf could not open %s: %s", path, exc)
        return None, "the file did not open as a PDF"


def read_outline(reader: Any) -> list[OutlineBookmark]:
    """Flatten the bookmark tree to (title, 1-based page, depth) rows.

    Broken destinations and empty titles are skipped: bookmarks are a
    best-effort enrichment, never a failure path. They are the only
    global structure a windowed parse can see.
    """
    bookmarks: list[OutlineBookmark] = []

    def walk(entries: object, depth: int) -> None:
        if not isinstance(entries, list):
            return
        for entry in entries:
            if isinstance(entry, list):
                walk(entry, depth + 1)
                continue
            title = getattr(entry, "title", None)
            if not isinstance(title, str) or not title.strip():
                continue
            try:
                page = reader.get_destination_page_number(entry)
            except Exception:
                continue
            if page >= 0:
                bookmarks.append(OutlineBookmark(title.strip(), page + 1, depth))

    try:
        with hush_pdf_logs():
            walk(reader.outline, 0)
    except Exception:
        return []
    return bookmarks


def parse_window_pypdf(reader: Any, start: int, end: int, warnings: list[str]) -> list[PageItem]:
    """Page-window text items (label ``text``); unreadable pages warn once."""
    items: list[PageItem] = []
    failed: list[int] = []
    with hush_pdf_logs():
        for page in range(start, end + 1):
            try:
                text = reader.pages[page - 1].extract_text() or ""
            except Exception:
                failed.append(page)
                continue
            if text.strip():
                items.append(PageItem(page, "text", text))
    if failed:
        skipped = ", ".join(str(page) for page in failed)
        warnings.append(f"pages {skipped} did not extract; skipped")
    return items


def load_docling_converter() -> Any | None:
    """A DocumentConverter, or None when the ``[docling]`` extra is absent.

    Dynamic import on purpose: the extra is optional and CI never installs
    it, so the import edge stays invisible to static resolution (the
    semchunk/code pattern).
    """
    try:
        module = importlib.import_module("docling.document_converter")
    except ImportError:
        return None
    return module.DocumentConverter()


def parse_window_docling(converter: Any, path: Path, start: int, end: int) -> list[PageItem]:
    """One window through Docling: per-page items with their labels.

    ``page_range`` is a 1-based inclusive ``(start, end)`` tuple, one
    range per ``convert`` call (the docling 2.x contract). Items without
    text or without a page number are dropped; labels pass through as
    text (``section_header`` drives the assembly half).
    """
    result = converter.convert(source=str(path), page_range=(start, end))
    items: list[PageItem] = []
    for node, _level in result.document.iterate_items():
        text = getattr(node, "text", None)
        prov = getattr(node, "prov", None)
        page = getattr(prov[0], "page_no", None) if prov else None
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(page, int) or isinstance(page, bool):
            continue
        items.append(PageItem(page, str(getattr(node, "label", "text")), text))
    return items
