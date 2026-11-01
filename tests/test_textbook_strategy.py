"""Textbook strategy: detection, page gate, windows, degrade, cache wiring.

The Docling leg runs against a fake converter double (same shapes:
``convert -> document.iterate_items`` with labeled, provenance-stamped
items), so windowing, the seam, and cache wiring test without the
``[docling]`` extra. Tests that exercise the direct converter call set
``window_subprocess: false``; tests for the child-process path patch the
wrapper seam (``parse_window_in_subprocess``) and never start a real
child. PDFs for the pypdf legs are generated in-test with pypdf — small
and deterministic, no committed fixtures. Every test that needs pypdf
skips when the ``[scan]`` extra is absent (CI installs neither extra);
one real-Docling leg skips without ``[docling]``. No test reaches the
network or downloads models.
"""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from corpus_kb.ingestion.base import (
    DETECT_ADOPT_CONFIDENCE,
    ChunkPlan,
    PlannedChunk,
    SourceProfile,
    validate_plan,
)
from corpus_kb.ingestion.registry import resolve_strategy, strategy_names
from corpus_kb.ingestion.strategies.textbook import TEXTBOOK_STRATEGY_NAME, TextbookStrategy
from corpus_kb.ingestion.strategies.textbook_pages import PageItem
from corpus_kb.ingestion.strategies.textbook_settings import settings_from

_PAGES_TARGET = "corpus_kb.ingestion.strategies.textbook.load_docling_converter"
_DEFAULT_TARGET = "corpus_kb.ingestion.strategies.textbook.DefaultStrategy"
_SUBPROCESS_TARGET = "corpus_kb.ingestion.strategies.textbook.parse_window_in_subprocess"
# Subprocess mode gates on the module spec (the parent never imports
# docling), so the child-path tests patch this seam, not the loader.
_SPEC_TARGET = "corpus_kb.ingestion.strategies.textbook.find_spec"


def _patch_docling_present(monkeypatch: pytest.MonkeyPatch, present: bool) -> None:
    monkeypatch.setattr(
        _SPEC_TARGET, lambda name: object() if present and name == "docling" else None
    )


_CFG: dict[str, object] = {"ingestion": {"strategies": {}, "default": "default"}}


def _profile(path: str, config: dict[str, object] | None = None) -> SourceProfile:
    return SourceProfile(source_type="textbook", is_file=True, path=path, config=config or _CFG)


def _config(
    min_pages: int, window_pages: int, cache_dir: str, parser: str = "docling", **extra: object
) -> dict:
    return {
        "ingestion": {"strategies": {}, "default": "default"},
        "textbook": {
            "min_pages": min_pages,
            "window_pages": window_pages,
            "parser": parser,
            "cache_dir": cache_dir,
            **extra,
        },
    }


class _StubDefault:
    """DefaultStrategy double: the fallback contract needs no real parse."""

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        return ChunkPlan(
            strategy="default",
            chunks=[PlannedChunk(text="default chunk", index=0)],
            document_text="default chunk",
        )


# ---------------------------------------------------------------------------
# Minimal PDF generation (pypdf writer; no committed fixtures)
# ---------------------------------------------------------------------------


def _minimal_pdf(page_texts: list[str]) -> bytes:
    """A hand-assembled single-font PDF: one text line per page."""
    objects: list[bytes] = []
    count = len(page_texts)
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(count))
    font_obj = 3 + 2 * count
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode())
    for i, text in enumerate(page_texts):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /Contents {4 + 2 * i} 0 R "
            f"/Resources << /Font << /F1 {font_obj} 0 R >> >> >>".encode()
        )
        stream = f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"
        objects.append(stream)
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n".encode()
    )
    return bytes(out)


def _write_pdf(
    path: Path, texts: list[str], bookmarks: list[tuple[str, int]] | None = None
) -> Path:
    """Write a small PDF; bookmarks are (title, 0-based page index) pairs."""
    pytest.importorskip("pypdf")
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(_minimal_pdf(texts)))
    writer = PdfWriter()
    writer.append(reader)
    for title, page_index in bookmarks or []:
        writer.add_outline_item(title, page_index)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


# ---------------------------------------------------------------------------
# Fake docling converter (the [docling] seam, no extra installed)
# ---------------------------------------------------------------------------


class _Prov:
    def __init__(self, page_no: int) -> None:
        self.page_no = page_no


class _Node:
    def __init__(self, label: str, text: str, page: int) -> None:
        self.label = label
        self.text = text
        self.prov = [_Prov(page)]


class _Document:
    def __init__(self, items: list[_Node]) -> None:
        self.items = items

    def iterate_items(self) -> list[tuple[_Node, float]]:
        return [(item, 0.0) for item in self.items]


class _Result:
    def __init__(self, items: list[_Node]) -> None:
        self.document = _Document(items)


class _FakeConverter:
    """DocumentConverter double fed from a page -> nodes map."""

    def __init__(self, pages: dict[int, list[_Node]]) -> None:
        self._pages = pages
        self.calls: list[tuple[int, int]] = []

    def convert(self, source: str, page_range: tuple[int, int] | None = None) -> _Result:
        assert page_range is not None and page_range[0] >= 1
        start, end = page_range
        self.calls.append((start, end))
        nodes = [node for page in range(start, end + 1) for node in self._pages.get(page, [])]
        return _Result(nodes)


def _fake_loader(pages: dict[int, list[_Node]]) -> Callable[[], _FakeConverter]:
    converter = _FakeConverter(pages)
    return lambda: converter


def _six_page_nodes() -> dict[int, list[_Node]]:
    """Two chapters over six pages; page bodies sized to split the pack."""
    body = "prose body " * 260  # ~2900 chars: one page near half the budget
    return {
        1: [_Node("section_header", "Chapter One", 1), _Node("text", "intro body", 1)],
        2: [_Node("text", body, 2)],
        3: [_Node("text", body, 3)],
        4: [_Node("section_header", "Chapter Two", 4), _Node("text", "new body", 4)],
        5: [_Node("text", body, 5)],
        6: [_Node("text", body, 6)],
    }


# ---------------------------------------------------------------------------
# Detection and registration
# ---------------------------------------------------------------------------


def test_detect_claims_pdf_suffix_only() -> None:
    result = TextbookStrategy().detect("text", "Big.Book.pdf", "")
    assert result.confidence >= DETECT_ADOPT_CONFIDENCE
    assert result.reason == "pdf-suffix"
    assert TextbookStrategy().detect("text", "notes.txt", "word " * 99).confidence == 0.0


def test_detect_hint_bumps_confidence() -> None:
    hinted = TextbookStrategy().detect(TEXTBOOK_STRATEGY_NAME, "book.pdf", "")
    plain = TextbookStrategy().detect("text", "book.pdf", "")
    assert hinted.confidence > plain.confidence


def test_strategy_is_registered_and_claims_pdfs() -> None:
    assert TEXTBOOK_STRATEGY_NAME in strategy_names()
    resolution = resolve_strategy("text", "book.pdf", "", _CFG)
    assert resolution.strategy.name == TEXTBOOK_STRATEGY_NAME
    assert resolution.basis == "detection"


def test_non_file_source_is_empty_but_conformant() -> None:
    plan = TextbookStrategy().build_chunks("raw", "x.pdf", SourceProfile("textbook", False, "x"))
    assert plan.chunks == []
    assert plan.empty_reason == "textbook parsing requires a file source"
    assert validate_plan(plan) == []


# ---------------------------------------------------------------------------
# min_pages gate and the default-plan fallback (stubbed default)
# ---------------------------------------------------------------------------


def test_below_min_pages_falls_back_to_default_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_DEFAULT_TARGET, _StubDefault)
    path = _write_pdf(tmp_path / "small.pdf", ["one", "two", "three"])
    plan = TextbookStrategy().build_chunks("", "small.pdf", _profile(str(path), _CFG))
    assert plan.strategy == TEXTBOOK_STRATEGY_NAME
    assert [chunk.text for chunk in plan.chunks] == ["default chunk"]
    assert any("below the 120 page minimum" in warning for warning in plan.warnings)
    assert validate_plan(plan) == []


def test_min_pages_gate_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_DEFAULT_TARGET, _StubDefault)
    path = _write_pdf(tmp_path / "edge.pdf", ["alpha body", "beta body"])
    below = TextbookStrategy().build_chunks(
        "", "edge.pdf", _profile(str(path), _config(3, 50, str(tmp_path / "c1"), "pypdf"))
    )
    assert [chunk.text for chunk in below.chunks] == ["default chunk"]
    assert any("below the 3 page minimum" in warning for warning in below.warnings)
    at_gate = TextbookStrategy().build_chunks(
        "", "edge.pdf", _profile(str(path), _config(2, 50, str(tmp_path / "c2"), "pypdf"))
    )
    assert at_gate.chunks
    assert all("below the" not in warning for warning in at_gate.warnings)
    assert all(chunk.metadata["page_start"] >= 1 for chunk in at_gate.chunks)


def test_unopenable_file_falls_back_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_DEFAULT_TARGET, _StubDefault)
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf at all")
    plan = TextbookStrategy().build_chunks("", "broken.pdf", _profile(str(path), _CFG))
    assert [chunk.text for chunk in plan.chunks] == ["default chunk"]
    assert any("did not open as a PDF" in warning for warning in plan.warnings)
    assert validate_plan(plan) == []


# ---------------------------------------------------------------------------
# pypdf windows: page metadata and outline enrichment
# ---------------------------------------------------------------------------


def test_pypdf_windows_carry_page_metadata(tmp_path: Path) -> None:
    path = _write_pdf(tmp_path / "book.pdf", [f"page {n} text" for n in range(1, 6)])
    config = _config(1, 2, str(tmp_path / "cache"), "pypdf")
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert len(plan.chunks) == 1  # one small section: all pages carry it
    chunk = plan.chunks[0]
    assert validate_plan(plan) == []
    assert chunk.metadata == {
        "strategy": TEXTBOOK_STRATEGY_NAME,
        "section": None,
        "page_start": 1,
        "page_end": 5,
    }
    assert plan.document_text[chunk.source_start_char : chunk.source_end_char] == chunk.text


def test_outline_bookmarks_fill_sections_on_a_real_pdf(tmp_path: Path) -> None:
    body = "sentence body text " * 260
    path = _write_pdf(
        tmp_path / "outlined.pdf",
        [body, body, body, body],
        bookmarks=[("Part One", 0), ("Part Two", 2)],
    )
    config = _config(1, 4, str(tmp_path / "cache"), "pypdf")
    plan = TextbookStrategy().build_chunks("", "outlined.pdf", _profile(str(path), config))
    assert validate_plan(plan) == []
    sections = {chunk.metadata["section"] for chunk in plan.chunks}
    assert sections == {"Part One", "Part Two"}
    part_two = [chunk for chunk in plan.chunks if chunk.metadata["section"] == "Part Two"]
    assert part_two and part_two[0].metadata["page_start"] == 3


# ---------------------------------------------------------------------------
# Docling windows through the fake converter: windows, seam, cache
# ---------------------------------------------------------------------------


def test_docling_windows_seam_and_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_pdf(tmp_path / "book.pdf", ["."] * 6)
    loader = _fake_loader(_six_page_nodes())
    monkeypatch.setattr(_PAGES_TARGET, loader)
    config = _config(1, 4, str(tmp_path / "cache"), window_subprocess=False)
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert loader().calls == [(1, 4), (5, 6)]
    assert validate_plan(plan) == []
    chapters = {chunk.metadata["section"] for chunk in plan.chunks}
    assert chapters == {"Chapter One", "Chapter Two"}
    seam = [chunk for chunk in plan.chunks if chunk.metadata["section"] == "Chapter One"]
    assert seam and seam[0].metadata["page_end"] == 3  # the carried page
    assert all(1 <= c.metadata["page_start"] <= c.metadata["page_end"] <= 6 for c in plan.chunks)


def test_window_parse_dedupes_a_repeated_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_pdf(tmp_path / "book.pdf", ["."] * 6)

    class _Bleeding(_FakeConverter):
        """Window 1 bleeds the next page's text (a parser overlap artifact)."""

        def convert(self, source: str, page_range: tuple[int, int] | None = None) -> _Result:
            result = super().convert(source, page_range)
            if page_range == (1, 4):
                return _Result([*result.document.items, _Node("text", "page five prose", 5)])
            return result

    pages = _six_page_nodes()
    pages[5] = [_Node("text", "marker-five " + pages[5][0].text, 5)]
    converter = _Bleeding(pages)
    monkeypatch.setattr(_PAGES_TARGET, lambda: converter)
    config = _config(1, 4, str(tmp_path / "cache"), window_subprocess=False)
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    texts = [chunk.text for chunk in plan.chunks]
    assert sum(text.count("page five prose") for text in texts) == 1
    assert not any("marker-five" in text for text in texts)  # first window wins


def test_cache_hits_skip_second_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_pdf(tmp_path / "book.pdf", ["."] * 6)
    loader = _fake_loader(_six_page_nodes())
    monkeypatch.setattr(_PAGES_TARGET, loader)
    cache_dir = tmp_path / "cache"
    config = _config(1, 4, str(cache_dir), window_subprocess=False)
    first = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert len(list(cache_dir.glob("docling-*.json"))) == 2

    def _boom() -> object:
        raise AssertionError("must use the cache")

    monkeypatch.setattr(_PAGES_TARGET, _boom)
    second = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert [chunk.text for chunk in second.chunks] == [chunk.text for chunk in first.chunks]


def test_cache_invalidates_when_the_file_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_pdf(tmp_path / "book.pdf", ["."] * 6)
    monkeypatch.setattr(_PAGES_TARGET, _fake_loader(_six_page_nodes()))
    config = _config(1, 4, str(tmp_path / "cache"), window_subprocess=False)
    TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    _write_pdf(path, ["changed body"] * 6)  # same page count, different bytes
    fresh = _fake_loader(_six_page_nodes())
    monkeypatch.setattr(_PAGES_TARGET, fresh)
    TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert len(fresh().calls) == 2  # the changed hash re-parsed both windows


def test_degrade_without_docling_extra_warns_and_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_pdf(tmp_path / "book.pdf", [f"body {n}" for n in range(1, 5)])
    monkeypatch.setattr(_PAGES_TARGET, lambda: None)
    _patch_docling_present(monkeypatch, present=False)  # subprocess mode reads the spec
    config = _config(1, 2, str(tmp_path / "cache"))
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert any("'docling' extra" in warning for warning in plan.warnings)
    assert plan.chunks
    assert validate_plan(plan) == []
    assert all(chunk.metadata["page_end"] >= chunk.metadata["page_start"] for chunk in plan.chunks)


def test_failed_convert_window_falls_back_to_pypdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_pdf(tmp_path / "book.pdf", ["alpha body", "beta body"])

    class _HalfBroken(_FakeConverter):
        def convert(self, source: str, page_range: tuple[int, int] | None = None) -> _Result:
            assert page_range is not None
            if page_range[0] > 1:
                raise RuntimeError("docling exploded")
            return super().convert(source, page_range)

    pages = {1: [_Node("section_header", "Chapter One", 1), _Node("text", "alpha body", 1)]}
    converter = _HalfBroken(pages)
    monkeypatch.setattr(_PAGES_TARGET, lambda: converter)
    config = _config(1, 1, str(tmp_path / "cache"), window_subprocess=False)
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert any("docling failed on pages 2 to 2" in warning for warning in plan.warnings)
    assert validate_plan(plan) == []
    assert any("beta body" in chunk.text for chunk in plan.chunks)


def test_unknown_parser_setting_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_PAGES_TARGET, lambda: None)  # keep the test hermetic
    _patch_docling_present(monkeypatch, present=False)  # subprocess mode reads the spec
    path = _write_pdf(tmp_path / "book.pdf", ["body"])
    config = _config(1, 50, str(tmp_path / "cache"), parser="ocr9000")
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert any("unknown textbook.parser 'ocr9000'" in warning for warning in plan.warnings)
    assert plan.chunks


# ---------------------------------------------------------------------------
# Child-process windows (issue #219): the wrapper seam, patched, never real
# ---------------------------------------------------------------------------


def test_subprocess_crash_falls_back_and_caches_under_pypdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_pdf(tmp_path / "book.pdf", [f"body {n}" for n in range(1, 5)])
    loader = _fake_loader(_six_page_nodes())
    monkeypatch.setattr(_PAGES_TARGET, loader)
    _patch_docling_present(monkeypatch, present=True)  # the spec gate passes; the child fails
    monkeypatch.setattr(_SUBPROCESS_TARGET, lambda pdf, start, end, timeout_s: None)
    cache_dir = tmp_path / "cache"
    config = _config(1, 2, str(cache_dir))
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert any("crashed or timed out in the child process" in warning for warning in plan.warnings)
    assert loader().calls == []  # the direct converter call never ran
    assert plan.chunks
    assert validate_plan(plan) == []
    assert list(cache_dir.glob("pypdf-*.json"))  # the fallback caches under pypdf
    assert not list(cache_dir.glob("docling-*.json"))


def test_subprocess_payload_feeds_the_docling_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_pdf(tmp_path / "book.pdf", ["."] * 6)
    loader = _fake_loader(_six_page_nodes())
    monkeypatch.setattr(_PAGES_TARGET, loader)
    _patch_docling_present(monkeypatch, present=True)  # the spec gate passes

    def _wrapper(pdf: Path, start: int, end: int, timeout_s: float) -> list[PageItem]:
        del pdf, timeout_s
        return [PageItem(page, "text", f"shipped page {page}") for page in range(start, end + 1)]

    monkeypatch.setattr(_SUBPROCESS_TARGET, _wrapper)
    cache_dir = tmp_path / "cache"
    config = _config(1, 4, str(cache_dir))
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert loader().calls == []
    assert list(cache_dir.glob("docling-*.json"))
    assert not list(cache_dir.glob("pypdf-*.json"))
    assert any("shipped page 2" in chunk.text for chunk in plan.chunks)


def test_subprocess_off_uses_the_direct_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_pdf(tmp_path / "book.pdf", ["."] * 6)
    loader = _fake_loader(_six_page_nodes())
    monkeypatch.setattr(_PAGES_TARGET, loader)

    def _must_not_spawn(*args: object) -> object:
        raise AssertionError("window_subprocess false must parse in-process")

    monkeypatch.setattr(_SUBPROCESS_TARGET, _must_not_spawn)
    config = _config(1, 4, str(tmp_path / "cache"), window_subprocess=False)
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert loader().calls == [(1, 4), (5, 6)]
    assert validate_plan(plan) == []


def test_cache_cap_prunes_before_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_pdf(tmp_path / "book.pdf", ["."] * 6)
    monkeypatch.setattr(_PAGES_TARGET, _fake_loader(_six_page_nodes()))
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    stale = cache_dir / "docling-aaaaaaaaaaaaaaaa-w0999.json"
    stale.write_bytes(b"x" * (2 * 1024 * 1024))
    os.utime(stale, (1000.0, 1000.0))  # ancient: the first file to evict
    config = _config(1, 4, str(cache_dir), cache_max_mb=1, window_subprocess=False)
    plan = TextbookStrategy().build_chunks("", "book.pdf", _profile(str(path), config))
    assert not stale.exists()  # the over-cap stale record went first
    assert list(cache_dir.glob("docling-*.json"))  # fresh windows still cached
    assert plan.chunks


# ---------------------------------------------------------------------------
# Error injection: the three documented swallow sites (issue 235). Bookmarks
# and page text are best-effort enrichment over a real PDF parse, so a
# failure there drops that piece and never fails the parse. Fakes only: no
# extra installed in CI.
# ---------------------------------------------------------------------------


class _OutlineEntry:
    def __init__(self, title: str, broken: bool = False) -> None:
        self.title = title
        self.broken = broken


def test_a_broken_bookmark_destination_drops_only_that_bookmark() -> None:
    """Injection at get_destination_page_number: one bad destination is skipped."""
    from corpus_kb.ingestion.strategies.textbook_pages import read_outline

    class _Reader:
        def __init__(self) -> None:
            self.outline = [_OutlineEntry("Bad", broken=True), _OutlineEntry("Good")]

        def get_destination_page_number(self, entry: _OutlineEntry) -> int:
            if entry.broken:
                raise ValueError("broken destination")
            return 4

    assert [(b.title, b.page, b.level) for b in read_outline(_Reader())] == [("Good", 5, 0)]


def test_a_failing_outline_read_returns_the_empty_sentinel() -> None:
    """Injection at the outline property: an unreadable outline yields no bookmarks."""
    from corpus_kb.ingestion.strategies.textbook_pages import read_outline

    class _Reader:
        @property
        def outline(self) -> list[object]:
            raise ValueError("malformed outline")

    assert read_outline(_Reader()) == []


class _ExtractPage:
    def __init__(self, text: str, broken: bool = False) -> None:
        self.text = text
        self.broken = broken

    def extract_text(self) -> str:
        if self.broken:
            raise OSError("font table blew up")
        return self.text


def test_a_failing_page_extraction_warns_and_keeps_the_rest() -> None:
    """Injection at extract_text: the bad page warns once; its neighbors parse."""
    from corpus_kb.ingestion.strategies.textbook_pages import parse_window_pypdf

    class _Reader:
        def __init__(self) -> None:
            self.pages = [
                _ExtractPage("first"),
                _ExtractPage("bad", broken=True),
                _ExtractPage("third"),
            ]

    warnings: list[str] = []
    items = parse_window_pypdf(_Reader(), 1, 3, warnings)
    assert [(item.page, item.text) for item in items] == [(1, "first"), (3, "third")]
    assert warnings == ["pages 2 did not extract; skipped"]


# ---------------------------------------------------------------------------
# Real legs (skip when the extra is absent — CI installs neither)
# ---------------------------------------------------------------------------


def test_real_pypdf_open_and_outline_probe(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    from corpus_kb.ingestion.strategies.textbook_pages import open_pdf, read_outline

    path = _write_pdf(
        tmp_path / "real.pdf", ["a", "b", "c"], bookmarks=[("Start", 0), ("Later", 2)]
    )
    reader, note = open_pdf(path)
    assert reader is not None and note is None
    assert len(reader.pages) == 3
    assert [(b.title, b.page) for b in read_outline(reader)] == [("Start", 1), ("Later", 3)]


def test_real_docling_leg(tmp_path: Path) -> None:
    pytest.importorskip("docling")
    path = _write_pdf(tmp_path / "real.pdf", ["plain body text", "more plain body text"])
    config = _config(1, 1, str(tmp_path / "cache"))
    plan = TextbookStrategy().build_chunks("", "real.pdf", _profile(str(path), config))
    assert plan.chunks
    assert validate_plan(plan) == []


def test_settings_helper_matches_config_block() -> None:
    assert settings_from({"textbook": {"min_pages": 5}}).min_pages == 5
    assert settings_from(None).window_pages == 50
