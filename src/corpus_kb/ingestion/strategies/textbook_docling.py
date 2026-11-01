"""The typed page-item contract and the Docling parse surface.

:class:`PageItem` is the one item shape for both parse surfaces: the
in-process converter and the isolated child both produce it, and the
window cache stores it. The positional fields (page, label, text) are
the contract every consumer reads; the rich fields are keyword-only
optionals. ``level`` and ``bbox`` come free with the Docling walk;
``table_markdown`` needs a table export call, so it fills only behind
the ``structured_output`` flag (default off). Items keep their first
page provenance; later provenances are not recorded (the page rule).

``PARSER_VERSIONS`` tags bake into cache records; :func:`parser_key`
derives the caller-side cache tag, so a flag flip caches the rich
variant under its own tag and never serves the other variant's records.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Version tags baked into cache records; a parser change invalidates the
#: records the old version wrote (the cache key carries this tag).
PARSER_VERSIONS: dict[str, str] = {"docling": "docling-v2", "pypdf": "pypdf-v1"}


@dataclass(frozen=True)
class PageItem:
    """One parsed block: its 1-based page, its parser label, its text.

    The rich fields are keyword-only and default to None, so the
    positional ``(page, label, text)`` construction keeps working.
    """

    page: int
    label: str
    text: str
    level: int | None = field(default=None, kw_only=True)
    table_markdown: str | None = field(default=None, kw_only=True)
    bbox: tuple[float, float, float, float] | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class RichFields:
    """The rich ``PageItem`` fields as parsed from a serialized row."""

    level: int | None = None
    table_markdown: str | None = None
    bbox: tuple[float, float, float, float] | None = None


def parser_key(parser: str, structured: bool) -> str:
    """The cache tag for one parser and structured-output flag pair.

    The flag changes the parse output, so the Docling rich variant
    caches under its own tag and a flag flip never serves the other
    variant's records. An unknown parser falls back to the Docling tag
    (the caller owns the warning).
    """
    base = PARSER_VERSIONS[parser] if parser in PARSER_VERSIONS else PARSER_VERSIONS["docling"]
    if structured and base == PARSER_VERSIONS["docling"]:
        return f"{base}-rich"
    return base


def load_docling_converter() -> Any | None:
    """A DocumentConverter, or None when the ``[docling]`` extra is absent.

    Dynamic import on purpose: the extra is optional and CI never
    installs it, so the import edge stays invisible to static checks.
    """
    try:
        module = importlib.import_module("docling.document_converter")
    except ImportError:
        return None
    return module.DocumentConverter()


def parse_window_docling(
    converter: Any, path: Path, start: int, end: int, structured: bool = False
) -> list[PageItem]:
    """One window through Docling: per-page items with their labels.

    ``page_range`` is a 1-based inclusive ``(start, end)`` tuple, one
    range per ``convert`` call (the docling 2.x contract). ``structured``
    adds the table markdown export per item.
    """
    result = converter.convert(source=str(path), page_range=(start, end))
    return items_from_convert_result(result, structured)


def items_from_convert_result(result: object, structured: bool = False) -> list[PageItem]:
    """Per-page items from one docling convert result.

    The one extraction rule for both parse surfaces: the in-process
    converter and the isolated child both call this, so their windows
    carry identical items. ``level`` and ``bbox`` fill on every item
    from the walk; ``table_markdown`` fills only when ``structured`` is
    set.
    """
    items: list[PageItem] = []
    document = getattr(result, "document", None)
    if document is None:
        return items
    for node, level in document.iterate_items():
        item = _item_from_walk(node, level, document, structured)
        if item is not None:
            items.append(item)
    return items


def _item_from_walk(
    node: object, level: object, document: object, structured: bool
) -> PageItem | None:
    """One walk row as a PageItem, or None when the item drops.

    Items without a page number or without content drop; a structured
    table without text carries its markdown instead. An item that spans
    several provenances keeps the first one.
    """
    prov = getattr(node, "prov", None)
    page = getattr(prov[0], "page_no", None) if prov else None
    if not isinstance(page, int) or isinstance(page, bool):
        return None
    text = getattr(node, "text", None)
    table_markdown = _table_markdown(node, document) if structured else None
    if isinstance(text, str) and text.strip():
        body = text
    elif table_markdown is not None:
        body = table_markdown
    else:
        return None
    return PageItem(
        page,
        str(getattr(node, "label", "text")),
        body,
        level=_level_from(level),
        table_markdown=table_markdown,
        bbox=_bbox_from(prov[0]) if prov else None,
    )


def _level_from(level: object) -> int | None:
    """The walk depth as an int, or None when the walk did not give one."""
    if isinstance(level, bool) or not isinstance(level, int):
        return None
    return level


def _bbox_from(prov: object) -> tuple[float, float, float, float] | None:
    """The ``(l, t, r, b)`` floats of a provenance's box, or None."""
    box = getattr(prov, "bbox", None)
    if box is None:
        return None
    try:
        return (float(box.l), float(box.t), float(box.r), float(box.b))
    except (TypeError, ValueError, AttributeError):
        return None


def _table_markdown(node: object, document: object) -> str | None:
    """The node's exported markdown, or None (not a table, or it failed).

    A failed export never fails the window: the item just carries no
    table markdown.
    """
    export = getattr(node, "export_to_markdown", None)
    if not callable(export):
        return None
    try:
        markdown = export(doc=document)
    except TypeError:
        try:
            markdown = export()  # older docling-core: no doc parameter yet
        except Exception:
            return None
    except Exception:
        return None
    if isinstance(markdown, str) and markdown.strip():
        return markdown
    return None


def page_row(item: PageItem) -> dict[str, object]:
    """The serialized row for one item; rich keys appear only when set.

    One row rule for both carriers: a pypdf row holds three keys, and a
    docling row also carries level/bbox once the walk provides them.
    """
    row: dict[str, object] = {"page": item.page, "label": item.label, "text": item.text}
    if item.level is not None:
        row["level"] = item.level
    if item.table_markdown is not None:
        row["table_markdown"] = item.table_markdown
    if item.bbox is not None:
        row["bbox"] = list(item.bbox)
    return row


class _MalformedRowError(Exception):
    """A rich row value failed its shape check (internal signal)."""


def _level_in(row: Mapping[str, object]) -> int | None:
    """The row's level, or None when absent; a bad shape raises."""
    raw = row.get("level")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _MalformedRowError
    return raw


def _table_in(row: Mapping[str, object]) -> str | None:
    """The row's table markdown, or None when absent; a bad shape raises."""
    raw = row.get("table_markdown")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise _MalformedRowError
    return raw


def _bbox_in(row: Mapping[str, object]) -> tuple[float, float, float, float] | None:
    """The row's box as four floats, or None when absent; a bad shape raises."""
    raw = row.get("bbox")
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 4:
        raise _MalformedRowError
    numbers: list[float] = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _MalformedRowError
        numbers.append(float(value))
    return (numbers[0], numbers[1], numbers[2], numbers[3])


def rich_fields_from_row(row: Mapping[str, object]) -> RichFields | None:
    """Rich fields from one serialized row; None on any malformed value.

    A missing key parses as None; one malformed value rejects the row
    shape, and each caller decides skip (child payload) or miss (cache).
    """
    try:
        return RichFields(level=_level_in(row), table_markdown=_table_in(row), bbox=_bbox_in(row))
    except _MalformedRowError:
        return None
