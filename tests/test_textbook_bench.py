"""Unit tests for the textbook bench metrics and driver (program W0).

All metric tests run on synthetic records — no PDF bytes anywhere. The
runner tests substitute fake readers, a fake pdfium module, and a fake
window-parse seam, so the suite stays hermetic and fast.
"""

from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.eval import textbook_bench as bench
from corpus_kb.eval import textbook_bench_run as bench_run
from corpus_kb.ingestion.base import TOKEN_BUDGET_CHARS, PlannedChunk
from corpus_kb.ingestion.strategies.textbook_assemble import (
    assign_sections,
    plan_chunks,
    section_blocks,
)
from corpus_kb.ingestion.strategies.textbook_pages import PageItem, WindowRecord

# ---------------------------------------------------------------------------
# Pure metrics
# ---------------------------------------------------------------------------


def test_normalized_distance_known_values() -> None:
    assert bench.normalized_distance("", "") == 0.0
    assert bench.normalized_distance("", "kitten") == 1.0
    assert bench.normalized_distance("kitten", "sitting") == pytest.approx(3 / 7, abs=1e-6)
    assert bench.normalized_distance("same", "same") == 0.0


def test_normalized_distance_ignores_whitespace_and_is_symmetric() -> None:
    left = "alpha\n\nbeta  gamma"
    right = "alpha beta gamma"
    assert bench.normalized_distance(left, right) == 0.0
    assert bench.normalized_distance(left, "beta gamma alpha") == bench.normalized_distance(
        "beta gamma alpha", left
    )


def test_page_fidelity_scores_layer_scope_with_missing_as_one() -> None:
    parsed = {1: "abc", 2: "xy"}
    layer = {1: "abd", 2: "zz", 3: "only the layer has this"}
    report = bench.page_fidelity(parsed, layer)
    assert report["compared_pages"] == 3
    assert report["parsed_pages"] == 2
    assert report["missing_pages"] == 1
    mean = (1 / 3 + 1.0 + 1.0) / 3
    assert report["mean_distance"] == pytest.approx(round(mean, 6), abs=1e-9)


def test_page_fidelity_dropping_pages_cannot_improve_the_mean() -> None:
    full = bench.page_fidelity({1: "abc", 2: "zz"}, {1: "abd", 2: "zz"})
    half = bench.page_fidelity({1: "abd"}, {1: "abd", 2: "zz"})
    assert full["mean_distance"] < half["mean_distance"]
    assert half["missing_pages"] == 1
    assert half["mean_distance"] == pytest.approx(0.5, abs=1e-9)


def test_page_fidelity_no_layer_pages_is_zeroed() -> None:
    report = bench.page_fidelity({1: "text"}, {})
    assert report == {
        "compared_pages": 0,
        "mean_distance": 0.0,
        "missing_pages": 0,
        "parsed_pages": 1,
    }


def test_section_f1_partial_overlap() -> None:
    detected = ["2.1 Bonds", "2.2 Water"]
    gold = ["2.1 bonds", "2.2 water", "2.3 Acids"]
    report = bench.section_f1(detected, gold)
    assert report["detected_titles"] == 2
    assert report["gold_titles"] == 3
    assert report["precision"] == 1.0
    assert report["recall"] == pytest.approx(2 / 3, abs=1e-6)
    assert report["f1"] == pytest.approx(0.8, abs=1e-6)


def test_section_f1_empty_sides_stay_zero() -> None:
    assert bench.section_f1([], [])["f1"] == 0.0
    assert bench.section_f1(["x"], [])["precision"] == 0.0


def _chunk(text: str, index: int = 0, page_start: int = 1, page_end: int = 1) -> PlannedChunk:
    return PlannedChunk(
        text=text,
        index=index,
        metadata={
            "strategy": "textbook",
            "section": "S",
            "page_start": page_start,
            "page_end": page_end,
        },
        source_start_char=0,
        source_end_char=len(text),
    )


def test_chunk_report_counts_and_conformance() -> None:
    small = "a" * 40  # 10 estimated tokens
    huge = "b" * (TOKEN_BUDGET_CHARS + 500)  # over the target token count
    report = bench.chunk_report([_chunk(small), _chunk(huge)], TOKEN_BUDGET_CHARS)
    assert report["count"] == 2
    assert report["budget_target_tokens"] == TOKEN_BUDGET_CHARS // 4
    assert report["budget_conformance"] == 0.5
    assert report["max_tokens"] > report["mean_tokens"] > 0


def test_chunk_report_empty_list_is_zeroed() -> None:
    report = bench.chunk_report([], TOKEN_BUDGET_CHARS)
    assert report["count"] == 0
    assert report["budget_conformance"] == 0.0
    assert report["max_tokens"] == 0


# ---------------------------------------------------------------------------
# Page attribution through the real assembly pipeline
# ---------------------------------------------------------------------------


def _pipeline(items: list[PageItem]) -> tuple[str, list[PlannedChunk]]:
    blocks = section_blocks(assign_sections(items, []))
    warnings: list[str] = []
    return plan_chunks(blocks, "textbook", TOKEN_BUDGET_CHARS, warnings)


def test_attribution_exact_when_no_block_splits() -> None:
    items = [
        PageItem(1, "section_header", "Chapter One"),
        PageItem(1, "text", "body words " * 20),
        PageItem(2, "text", "more body words " * 20),
    ]
    document_text, chunks = _pipeline(items)
    rebuilt, spans = bench.rebuild_pages(items)
    report = bench.attribution_report(chunks, spans, rebuilt == document_text)
    assert report["join_matches_document_text"] is True
    assert report["exact_rate"] == 1.0
    assert report["exact"] == report["chunks"]


def test_attribution_flags_split_pieces_claiming_whole_block() -> None:
    # One section flowing over two pages, long enough to force a split;
    # every split piece claims the full block span, so the head and tail
    # pieces lie while only the middle piece spans both pages.
    body = "word " * 1600  # two 8000-char pages; the merged block splits in three
    items = [PageItem(1, "text", body), PageItem(2, "text", body)]
    _document_text, chunks = _pipeline(items)
    _rebuilt, spans = bench.rebuild_pages(items)
    report = bench.attribution_report(chunks, spans, True)
    assert report["chunks"] >= 3
    assert report["exact"] < report["chunks"]
    assert report["exact_rate"] < 1.0
    trues = {tuple(example["true"]) for example in report["mismatch_examples"]}
    assert trues == {(1, 1), (2, 2)}
    assert all(example["claimed"] == [1, 2] for example in report["mismatch_examples"])


def test_rebuild_pages_drops_blank_items_and_orders_by_page() -> None:
    items = [PageItem(2, "text", "second"), PageItem(1, "text", "  "), PageItem(1, "text", "first")]
    rebuilt, spans = bench.rebuild_pages(items)
    assert rebuilt == "first\n\nsecond"
    assert [span[0] for span in spans] == [1, 2]
    assert spans[0] == (1, 0, 5)
    assert spans[1] == (2, 7, 13)


def test_attribution_report_empty_chunks() -> None:
    report = bench.attribution_report([], [(1, 0, 5)], False)
    assert report["chunks"] == 0
    assert report["exact_rate"] == 0.0
    assert report["join_matches_document_text"] is False


def test_attribution_chunk_covering_no_page_is_always_a_mismatch() -> None:
    # Char range outside every span, claimed metadata equally empty:
    # (0, 0) == (0, 0) must not count as exact.
    chunk = _chunk("orphan", page_start=0, page_end=0)
    chunk.source_start_char = 99999
    chunk.source_end_char = 100000
    report = bench.attribution_report([chunk], [(1, 0, 5)], True)
    assert report["exact"] == 0
    assert report["exact_rate"] == 0.0
    assert report["mismatch_examples"][0]["true"] == [0, 0]


# ---------------------------------------------------------------------------
# Text layer and run_book with fakes
# ---------------------------------------------------------------------------


class _FakeTextPage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text_range(self) -> str:
        return self._text


class _FakePdfPage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_textpage(self) -> _FakeTextPage:
        return _FakeTextPage(self._text)


class _FakePdfiumDoc:
    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.closed = False

    def __getitem__(self, index: int) -> _FakePdfPage:
        return _FakePdfPage(self._texts[index])

    def close(self) -> None:
        self.closed = True


def _install_fake_pdfium(monkeypatch: pytest.MonkeyPatch, texts: list[str] | Exception) -> None:
    if isinstance(texts, Exception):

        def open_fail(_path: str) -> _FakePdfiumDoc:
            raise texts

        module = types.SimpleNamespace(PdfDocument=open_fail)
    else:
        module = types.SimpleNamespace(PdfDocument=lambda _path: _FakePdfiumDoc(texts))
    monkeypatch.setitem(sys.modules, "pypdfium2", module)


class _FakeDest:
    def __init__(self, title: str) -> None:
        self.title = title


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakeReader:
    """A pypdf-shaped reader: pages plus a nested outline tree."""

    def __init__(self, page_texts: list[str], outline: dict[str, int] | None = None) -> None:
        self.pages = [_FakePage(text) for text in page_texts]
        anchors = outline or {}
        self.outline: list[Any] = [[_FakeDest(title) for title in anchors]]
        self._anchor_pages = anchors

    def get_destination_page_number(self, dest: _FakeDest) -> int:
        return self._anchor_pages[dest.title]


def test_text_layer_uses_pdfium_when_importable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_pdfium(monkeypatch, ["one", "two"])
    texts, name = bench_run.text_layer(tmp_path / "book.pdf", _FakeReader([]), 2)
    assert name == "pypdfium2"
    assert texts == {1: "one", 2: "two"}


def test_text_layer_falls_back_when_pdfium_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_pdfium(monkeypatch, RuntimeError("pdfium broke"))
    reader = _FakeReader(["alpha", "beta"])
    texts, name = bench_run.text_layer(tmp_path / "book.pdf", reader, 2)
    assert name == "pypdf-fallback"
    assert texts == {1: "alpha", 2: "beta"}


def test_text_layer_falls_back_when_pdfium_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_missing(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr(bench_run.importlib, "import_module", raise_missing)
    texts, name = bench_run.text_layer(tmp_path / "book.pdf", _FakeReader(["z"]), 1)
    assert name == "pypdf-fallback"
    assert texts == {1: "z"}


def _patch_parse(monkeypatch: pytest.MonkeyPatch, items: list[PageItem]) -> list[Any]:
    seen: list[Any] = []

    def fake_windows(
        path: Path,
        reader: Any,
        digest: str,
        page_count: int,
        parser: str,
        cache_dir: Path,
        window_pages: int,
        warnings: list[str],
    ) -> list[WindowRecord]:
        del reader, digest, parser, cache_dir, window_pages
        seen.append((path, page_count, warnings))
        return [WindowRecord(1, page_count, items)]

    monkeypatch.setattr(bench_run, "_parse_windows", fake_windows)
    return seen


def test_run_book_scores_a_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    items = [
        PageItem(1, "section_header", "Chapter One"),
        PageItem(1, "text", "body text " * 30),
        PageItem(2, "text", "second page words " * 30),
    ]
    seen = _patch_parse(monkeypatch, items)
    monkeypatch.setattr(
        bench_run,
        "open_pdf",
        lambda _path: (_FakeReader(["x", "y"], {"Chapter One": 0}), None),
    )
    _install_fake_pdfium(monkeypatch, ["body text " * 30, "second page words " * 30])
    report = bench_run.run_book(pdf, "pypdf", tmp_path / "cache")
    assert "error" not in report
    assert report["parser"] == "pypdf"
    assert report["page_count"] == 2
    assert report["parsed_pages"] == 2
    assert report["text_layer"] == "pypdfium2"
    assert report["sha256"] == hashlib.sha256(b"%PDF-1.4 fake").hexdigest()
    assert report["parse_seconds"] >= 0.0
    assert seen and seen[0][1] == 2
    assert report["attribution"]["join_matches_document_text"] is True
    assert report["fidelity"]["compared_pages"] == 2
    assert report["sections"]["gold_titles"] == 1


def test_run_book_reports_an_unopenable_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bench_run, "open_pdf", lambda _path: (None, "the file did not open as a PDF")
    )
    report = bench_run.run_book(tmp_path / "bad.pdf", "pypdf", tmp_path / "cache")
    assert "the pdf did not open" in str(report["error"])


def test_parse_windows_without_docling_falls_back_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = _FakeReader(["one", "two", "three"])
    monkeypatch.setattr(bench_run, "load_docling_converter", lambda: None)
    warnings: list[str] = []
    records = bench_run._parse_windows(
        Path("book.pdf"), reader, "d" * 64, 3, "docling", tmp_path, 2, warnings
    )
    assert any("docling" in warning for warning in warnings)
    pages = [item.page for record in records for item in record.items]
    assert pages == [1, 2, 3]
    assert all(item.label == "text" for item in records[0].items)


def test_parse_windows_docling_success_caches_under_docling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = _FakeReader(["one", "two"])
    monkeypatch.setattr(bench_run, "load_docling_converter", lambda: object())
    monkeypatch.setattr(
        bench_run,
        "parse_window_docling",
        lambda _converter, _path, start, end: [PageItem(start, "section_header", "Header")],
    )
    warnings: list[str] = []
    records = bench_run._parse_windows(
        Path("book.pdf"), reader, "d" * 64, 2, "docling", tmp_path, 1, warnings
    )
    assert warnings == []
    assert records[0].items[0].label == "section_header"
    assert list(tmp_path.glob("docling-*-w0000.json")), "the window must cache under docling"
    cached = bench_run._cache_lookup(tmp_path, "docling", "d" * 64, 0, 1, 1)
    assert cached is not None
    assert cached.items[0].label == "section_header"


def test_parse_windows_docling_crash_falls_back_per_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = _FakeReader(["one", "two"])
    monkeypatch.setattr(bench_run, "load_docling_converter", lambda: object())

    def crash(_converter: object, _path: Path, _start: int, _end: int) -> list[PageItem]:
        raise RuntimeError("docling blew up")

    monkeypatch.setattr(bench_run, "parse_window_docling", crash)
    warnings: list[str] = []
    records = bench_run._parse_windows(
        Path("book.pdf"), reader, "d" * 64, 2, "docling", tmp_path, 1, warnings
    )
    assert any("used pypdf" in warning for warning in warnings)
    assert [item.page for record in records for item in record.items] == [1, 2]


def test_run_book_max_pages_caps_span_and_gold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    items = [PageItem(1, "text", "early words")]
    seen = _patch_parse(monkeypatch, items)
    monkeypatch.setattr(
        bench_run,
        "open_pdf",
        lambda _path: (_FakeReader(["a", "b", "c"], {"Late Chapter": 2}), None),
    )
    _install_fake_pdfium(monkeypatch, ["early words", "", ""])
    report = bench_run.run_book(pdf, "pypdf", tmp_path / "cache", max_pages=1)
    assert report["page_count"] == 3
    assert report["parsed_pages"] == 1
    assert seen[0][1] == 1
    assert report["sections"]["gold_titles"] == 0  # the late bookmark is out of span


# ---------------------------------------------------------------------------
# CLI driver
# ---------------------------------------------------------------------------


def _fake_run_book(
    path: Path, parser: str, cache_dir: Path, window_pages: int, max_pages: int
) -> dict:
    del cache_dir, window_pages, max_pages
    return {"file": str(path), "parser": parser, "parse_seconds": 1.0}


def test_main_writes_deterministic_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench_run, "run_book", _fake_run_book)
    book = tmp_path / "book.pdf"
    book.write_bytes(b"pdf")
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    code_a = bench.main(["--pdf", str(book), "--out", str(out_a)])
    code_b = bench.main(["--pdf", str(book), "--out", str(out_b)])
    assert code_a == 0 and code_b == 0
    assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")
    payload = json.loads(out_a.read_text(encoding="utf-8"))
    assert payload["parser"] == "pypdf"
    assert payload["books"][0]["file"] == str(book)
    assert "stamp_utc" not in payload


def test_main_stamp_adds_timestamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench_run, "run_book", _fake_run_book)
    book = tmp_path / "book.pdf"
    book.write_bytes(b"pdf")
    out = tmp_path / "stamped.json"
    assert bench.main(["--pdf", str(book), "--out", str(out), "--stamp"]) == 0
    assert "stamp_utc" in json.loads(out.read_text(encoding="utf-8"))


def test_main_without_books_exits_two(tmp_path: Path) -> None:
    assert bench.main(["--books-dir", str(tmp_path / "empty")]) == 2


def test_main_book_error_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def failing(
        path: Path, parser: str, cache_dir: Path, window_pages: int, max_pages: int
    ) -> dict:
        del parser, cache_dir, window_pages, max_pages
        return {"error": "the pdf did not open: broken", "file": str(path)}

    monkeypatch.setattr(bench_run, "run_book", failing)
    book = tmp_path / "book.pdf"
    book.write_bytes(b"pdf")
    out = tmp_path / "failed.json"
    assert bench.main(["--pdf", str(book), "--out", str(out)]) == 1
    assert "every book failed" in capsys.readouterr().err
    assert "error" in json.loads(out.read_text(encoding="utf-8"))["books"][0]


def test_main_golden_books_from_env_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bench_run, "run_book", _fake_run_book)
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    monkeypatch.setenv("CORPUS_KB_TEXTBOOK_GOLDEN_DIR", str(golden_dir))
    for book in bench.load_manifest():
        (golden_dir / book.filename).write_bytes(b"pdf")
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "more.pdf").write_bytes(b"pdf")
    seen: dict[str, int] = {}

    def counting(path: Path, *args: Any, **kwargs: Any) -> dict:
        del args, kwargs
        seen[str(path)] = seen.get(str(path), 0) + 1
        return {"file": str(path)}

    monkeypatch.setattr(bench_run, "run_book", counting)
    code = bench.main(["--golden", "--books-dir", str(extra)])
    assert code == 0
    assert len(seen) == len(bench.load_manifest()) + 1
