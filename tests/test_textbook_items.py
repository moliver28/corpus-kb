"""The PageItem contract, the rich adapter fields, and the parser tags.

The Docling leg runs against fakes shaped like the real walk
(``convert -> document.iterate_items`` with levels, provenance boxes,
and table nodes that export markdown). The rich fields follow the W1-B
rule: level and bbox fill on every item, table markdown fills only
behind ``structured_output``. A row holds three keys only when the
item set nothing; a docling item's row carries level/bbox flag-off too.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.ingestion.strategies.textbook_docling import (
    PARSER_VERSIONS,
    PageItem,
    RichFields,
    page_row,
    parser_key,
    rich_fields_from_row,
)
from corpus_kb.ingestion.strategies.textbook_docling import (
    items_from_convert_result as adapter,
)


class _Box:
    """A provenance box double shaped like docling's (l, t, r, b attrs)."""

    def __init__(self, left: float, t: float, r: float, b: float) -> None:
        self.l, self.t, self.r, self.b = left, t, r, b


class _Prov:
    def __init__(self, page_no: int, bbox: object | None = None) -> None:
        self.page_no = page_no
        self.bbox = bbox


class _Node:
    """One walk node: label, text, one provenance row."""

    def __init__(self, label: str, text: str, page: int, bbox: object | None = None) -> None:
        self.label = label
        self.text = text
        self.prov = [_Prov(page, bbox)]


class _TableNode(_Node):
    """A table node: real docling tables export markdown, not ``text``."""

    def __init__(self, page: int, markdown: str, text: str = "") -> None:
        super().__init__("table", text, page)
        self._markdown = markdown

    def export_to_markdown(self, doc: object = None) -> str:
        del doc
        return self._markdown


class _BrokenTableNode(_TableNode):
    """A table whose export fails: the item must survive without markdown."""

    def export_to_markdown(self, doc: object = None) -> str:
        del doc
        raise RuntimeError("export exploded")


class _Document:
    """iterate_items double: emits (node, depth) rows in list order."""

    def __init__(self, rows: list[tuple[object, object]]) -> None:
        self.rows = rows

    def iterate_items(self) -> list[tuple[object, object]]:
        return list(self.rows)


class _Result:
    def __init__(self, rows: list[tuple[object, object]]) -> None:
        self.document = _Document(rows)


# ---------------------------------------------------------------------------
# The item contract: legacy shape and rich fields
# ---------------------------------------------------------------------------


def test_legacy_positional_construction_keeps_working() -> None:
    item = PageItem(3, "section_header", "Chapter One")
    assert (item.page, item.label, item.text) == (3, "section_header", "Chapter One")
    assert item.level is None
    assert item.table_markdown is None
    assert item.bbox is None


def test_rich_fields_are_keyword_only() -> None:
    item = PageItem(1, "table", "| a |", level=2, table_markdown="| a |", bbox=(1.0, 2.0, 3.0, 4.0))
    assert (item.level, item.table_markdown, item.bbox) == (2, "| a |", (1.0, 2.0, 3.0, 4.0))
    legacy_plus_rich: Any = (1, "table", "| a |", 2)
    with pytest.raises(TypeError):
        PageItem(*legacy_plus_rich)  # a rich field never passes positionally


# ---------------------------------------------------------------------------
# Adapter rich fields: level and bbox always, tables behind the flag
# ---------------------------------------------------------------------------


def _rich_result() -> _Result:
    box = _Box(1.0, 2.0, 3.0, 4.0)
    return _Result(
        [
            (_Node("section_header", "Chapter One", 1, bbox=box), 1),
            (_Node("text", "intro body", 1, bbox=box), 2),
            (_Node("text", "  ", 2), 2),  # blank: dropped either way
            (_TableNode(3, "| a | b |\n| - | - |\n| 1 | 2 |"), 2),
        ]
    )


def test_adapter_fills_level_and_bbox_on_every_item() -> None:
    items = adapter(_rich_result())
    assert [(item.page, item.label, item.text) for item in items] == [
        (1, "section_header", "Chapter One"),
        (1, "text", "intro body"),
    ]
    assert [item.level for item in items] == [1, 2]
    assert [item.bbox for item in items] == [(1.0, 2.0, 3.0, 4.0), (1.0, 2.0, 3.0, 4.0)]
    assert all(item.table_markdown is None for item in items)  # flag off


def test_table_markdown_populates_only_when_structured() -> None:
    off = adapter(_rich_result())
    assert all(item.table_markdown is None for item in off)
    assert all(item.page != 3 for item in off)  # the textless table still drops

    on = adapter(_rich_result(), structured=True)
    table = [item for item in on if item.label == "table"]
    assert len(table) == 1
    assert table[0].text == "| a | b |\n| - | - |\n| 1 | 2 |"
    assert table[0].table_markdown == table[0].text
    assert table[0].page == 3


def test_table_with_own_text_keeps_text_and_gains_markdown() -> None:
    result = _Result([(_TableNode(4, "| a |", text="body"), 1)])
    item = adapter(result, structured=True)[0]
    assert (item.text, item.table_markdown) == ("body", "| a |")


def test_multi_provenance_item_keeps_the_first() -> None:
    node = _Node("text", "spans two pages", 7)
    node.prov = [_Prov(7, _Box(0.0, 0.0, 1.0, 1.0)), _Prov(8, _Box(9.0, 9.0, 9.0, 9.0))]
    item = adapter(_Result([(node, 1)]), structured=True)[0]
    assert (item.page, item.bbox) == (7, (0.0, 0.0, 1.0, 1.0))


def test_flag_off_matches_the_legacy_adapter_byte_for_byte() -> None:
    # A legacy-shaped walk: float depths, no boxes, no table exports.
    class _LegacyProv:
        page_no = 5

    class _LegacyNode:
        label = "text"
        text = "plain body"

        def __init__(self) -> None:
            self.prov = [_LegacyProv()]

    class _LegacyDocument:
        def iterate_items(self) -> list[tuple[_LegacyNode, float]]:
            return [(_LegacyNode(), 0.0)]

    class _LegacyResult:
        document = _LegacyDocument()

    legacy = [PageItem(5, "text", "plain body")]
    assert adapter(_LegacyResult()) == legacy
    assert adapter(_LegacyResult(), structured=False) == legacy


def test_malformed_rich_metadata_degrades_to_none() -> None:
    class _BadBox:
        l, t, r, b = 1.0, "high", 3.0, 4.0  # noqa: E741  # float() on t raises ValueError

    result = _Result(
        [
            (_Node("text", "odd body", 2, bbox=_BadBox()), 1.5),  # float depth
            (_BrokenTableNode(9, "| a |", text="kept body"), 1),
        ]
    )
    items = adapter(result, structured=True)
    assert [item.text for item in items] == ["odd body", "kept body"]
    assert items[0].level is None
    assert items[0].bbox is None
    assert items[1].table_markdown is None  # the export failed, the item stayed


def test_missing_document_yields_no_items() -> None:
    assert adapter(object()) == []


# ---------------------------------------------------------------------------
# parser_key: the caller-side cache tag
# ---------------------------------------------------------------------------


def test_parser_versions_bump_docling_and_keep_pypdf() -> None:
    assert PARSER_VERSIONS == {"docling": "docling-v2", "pypdf": "pypdf-v1"}


def test_parser_key_variants() -> None:
    assert parser_key("docling", False) == "docling-v2"
    assert parser_key("docling", True) == "docling-v2-rich"
    assert parser_key("pypdf", False) == "pypdf-v1"
    assert parser_key("pypdf", True) == "pypdf-v1"  # pypdf has no rich fields
    assert parser_key("ocr9000", False) == "docling-v2"  # unknown: docling tag
    assert parser_key("ocr9000", True) == "docling-v2-rich"


# ---------------------------------------------------------------------------
# The shared row rule: serialize only what is set, parse it back
# ---------------------------------------------------------------------------


def test_page_row_serializes_rich_keys_only_when_set() -> None:
    plain = page_row(PageItem(2, "text", "body"))
    assert plain == {"page": 2, "label": "text", "text": "body"}
    rich = page_row(
        PageItem(3, "table", "| a |", level=4, table_markdown="| a |", bbox=(1.0, 2.0, 3.0, 4.0))
    )
    assert rich == {
        "page": 3,
        "label": "table",
        "text": "| a |",
        "level": 4,
        "table_markdown": "| a |",
        "bbox": [1.0, 2.0, 3.0, 4.0],
    }


def test_rich_fields_round_trip_through_a_row() -> None:
    row = page_row(
        PageItem(1, "text", "b", level=3, table_markdown="| x |", bbox=(1.0, 2.0, 3.0, 4.0))
    )
    parsed = rich_fields_from_row(row)
    assert parsed == RichFields(level=3, table_markdown="| x |", bbox=(1.0, 2.0, 3.0, 4.0))
    assert rich_fields_from_row({"page": 1, "label": "text", "text": "b"}) == RichFields()


def test_rich_fields_reject_malformed_values() -> None:
    assert rich_fields_from_row({"level": True}) is None  # bools are not levels
    assert rich_fields_from_row({"level": "deep"}) is None
    assert rich_fields_from_row({"table_markdown": 7}) is None
    assert rich_fields_from_row({"bbox": [1.0, 2.0, 3.0]}) is None  # three coords
    assert rich_fields_from_row({"bbox": [1.0, 2.0, 3.0, "x"]}) is None
    assert rich_fields_from_row({"bbox": (1.0, 2.0, 3.0, 4.0)}) is None  # list, not tuple
