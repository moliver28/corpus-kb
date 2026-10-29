"""The textbook candidacy scanner (textbook program W2C).

Two tiers. The signal tier runs on fake readers (no bytes, no pypdf):
weights, band filter, sampling, regex caps, Jaccard overlap, and the
deterministic sort. The bytes tier writes tiny real PDFs through pypdf
(skipped when the [scan] extra is absent) and pins the reader round trip.
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.errors import DependencyMissingError, ValidationError
from corpus_kb.ingestion.textbook_scan import (
    BAND_MAX_PAGES,
    SAMPLE_PAGES,
    _sample_indices,
    scan_path,
    scan_pdf,
)
from corpus_kb.ingestion.textbook_signals import SCAN_WEIGHTS, top_tokens

# ---------------------------------------------------------------------------
# Fake reader: the signal tier needs no bytes
# ---------------------------------------------------------------------------


@dataclass
class _Page:
    text: str

    def extract_text(self) -> str:
        return self.text


class _FakeReader:
    def __init__(self, page_texts: list[str], outline: list[str] | None = None) -> None:
        self.pages = [_Page(text) for text in page_texts]
        self.outline: list[Any] = outline or []

    def __call__(self, path: Path) -> _FakeReader:
        return self


FILLER = (
    "The plain page body text carries common words and repeats them so the "
    "extractable length clears the text threshold on every sampled page. "
    "More filler follows about the plateau region and its rivers and soils."
)


def _book_texts(pages: int = 130, page_text: str = FILLER) -> list[str]:
    return [page_text for _ in range(pages)]


TEXTBOOK_PAGE = (
    "A plateau is defined as a flat landform. We will learn about erosion. "
    "See chapter 3 for rivers, and table 7 for soils.\n"
    "col1  col2  col3\n"
    "The body text continues across the page and mentions the plateau of the "
    "region, the rivers that cut it, and the soils that cover its terraces. "
    "Recall that the glossary defines every term in this chapter again.\n"
)
INDEX_PAGE = "Index\nplateau....312\nerosion.....77\nrivers......401\nGlossary\n"


# ---------------------------------------------------------------------------
# Weights, band, sampling
# ---------------------------------------------------------------------------


def test_weights_sum_to_one() -> None:
    assert abs(sum(SCAN_WEIGHTS.values()) - 1.0) < 1e-9
    assert SCAN_WEIGHTS["text_layer"] == 0.20
    assert SCAN_WEIGHTS["outline"] == 0.20
    assert SCAN_WEIGHTS["overlap"] == 0.15


def test_sample_indices_are_evenly_spaced_and_bounded() -> None:
    assert _sample_indices(5) == [0, 1, 2, 3, 4]
    spread = _sample_indices(1300)
    assert len(spread) == SAMPLE_PAGES
    assert spread == sorted(set(spread))
    assert spread[0] == 0 and spread[-1] == 1299


def test_below_the_band_is_not_a_candidate() -> None:
    reader = _FakeReader(_book_texts(5))
    result = scan_pdf("book.pdf", reader_factory=reader)
    assert result.pages == 5
    assert result.candidate is False
    assert result.score == 0.0
    assert "band" in (result.detail or "")


def test_above_the_band_is_not_a_candidate() -> None:
    reader = _FakeReader(_book_texts(BAND_MAX_PAGES + 1))
    result = scan_pdf("book.pdf", reader_factory=reader)
    assert result.candidate is False
    assert result.pages == BAND_MAX_PAGES + 1


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


def test_a_rich_book_scores_high_with_pinned_signals() -> None:
    texts = _book_texts(130, TEXTBOOK_PAGE)
    texts[-1] = INDEX_PAGE
    reader = _FakeReader(texts, outline=["Chapter One", "Chapter Two"])
    result = scan_pdf("book.pdf", reader_factory=reader)
    assert result.candidate is True
    signals = result.signals
    # One sampled page is the short index page: 11 of 12 pages clear the bar.
    assert signals["text_layer"] == pytest.approx(11 / 12, abs=0.01)
    assert signals["outline"] == 1.0
    assert 0.0 < signals["definitions"] <= 1.0
    assert signals["xrefs"] > 0.0
    assert signals["hedging"] > 0.0
    assert signals["tables"] > 0.0
    assert signals["index_glossary"] > 0.0  # the index page lands in the sample
    assert signals["overlap"] > 0.8  # identical pages, one index page
    assert result.score > 0.5


def test_a_plain_book_scores_on_text_and_overlap_only() -> None:
    reader = _FakeReader(_book_texts(130))
    result = scan_pdf("book.pdf", reader_factory=reader)
    assert result.candidate is True
    assert result.signals["text_layer"] == 1.0
    assert result.signals["outline"] == 0.0
    assert result.signals["definitions"] == 0.0
    assert result.signals["overlap"] == 1.0
    assert result.score == pytest.approx(0.35, abs=0.01)


def test_short_pages_degrade_the_text_layer_signal() -> None:
    texts = [""] * 130  # a scanned book: no extractable text anywhere
    reader = _FakeReader(texts)
    result = scan_pdf("book.pdf", reader_factory=reader)
    assert result.signals["text_layer"] == 0.0
    assert result.signals["overlap"] == 0.0
    assert result.score == 0.0


def test_top_tokens_drop_stopwords_and_rank_by_count() -> None:
    tokens = top_tokens("plateau plateau plateau erosion river glossary the and it")
    assert "plateau" in tokens
    assert "the" not in tokens and "and" not in tokens
    assert len(tokens) <= 25


def test_distinct_pages_lower_the_neighbor_overlap() -> None:
    tails = ["north", "south", "east", "west", "coast", "delta"]
    texts = [f"{FILLER} The {tails[i % len(tails)]} chapter ends here." for i in range(130)]
    reader = _FakeReader(texts)
    result = scan_pdf("book.pdf", reader_factory=reader)
    assert 0.0 < result.signals["overlap"] < 1.0


# ---------------------------------------------------------------------------
# Robustness and determinism
# ---------------------------------------------------------------------------


def test_an_unreadable_pdf_scores_zero_with_a_note() -> None:
    def _broken(path: Path) -> Any:
        raise ValueError("bad bytes")

    result = scan_pdf("broken.pdf", reader_factory=_broken)
    assert result.score == 0.0
    assert result.detail == "unreadable: ValueError"


def test_a_missing_pypdf_reads_typed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "pypdf", None)
    with pytest.raises(DependencyMissingError):
        scan_pdf(tmp_path / "book.pdf")


def test_a_missing_scan_path_raises_validation_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        scan_path(tmp_path / "nope")


def test_scan_sorts_by_score_desc_then_path_asc(tmp_path: Path) -> None:
    rich = _FakeReader(_book_texts(130, TEXTBOOK_PAGE), outline=["One"])
    plain = _FakeReader(_book_texts(130))
    (tmp_path / "b_plain.pdf").write_bytes(b"ignored")
    (tmp_path / "c_plain.pdf").write_bytes(b"ignored")
    (tmp_path / "a_rich.pdf").write_bytes(b"ignored")
    results = scan_path(
        tmp_path,
        reader_factory=lambda path: rich if path.name == "a_rich.pdf" else plain,
    )
    assert [r.path for r in results] == [
        str(tmp_path / "a_rich.pdf"),
        str(tmp_path / "b_plain.pdf"),
        str(tmp_path / "c_plain.pdf"),
    ]
    assert results[0].score > results[1].score == results[2].score


def test_same_score_ties_break_by_path(tmp_path: Path) -> None:
    plain = _FakeReader(_book_texts(130))
    (tmp_path / "z.pdf").write_bytes(b"x")
    (tmp_path / "a.pdf").write_bytes(b"x")
    results = scan_path(tmp_path, reader_factory=plain)
    assert [Path(r.path).name for r in results] == ["a.pdf", "z.pdf"]


# ---------------------------------------------------------------------------
# Bytes tier: real PDFs through pypdf (skipped without the [scan] extra)
# ---------------------------------------------------------------------------


def _page_pdf_bytes(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode()
    )
    return out.getvalue()


def _write_pdf(path: Path, page_texts: list[str], outline: list[int] | None = None) -> Path:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for text in page_texts:
        writer.append(PdfReader(io.BytesIO(_page_pdf_bytes(text))))
    for page_index in outline or []:
        writer.add_outline_item(f"Chapter {page_index}", page_index)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def test_a_real_textbook_pdf_round_trips(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    path = _write_pdf(
        tmp_path / "book.pdf",
        [*_book_texts(130, TEXTBOOK_PAGE)[:-1], INDEX_PAGE],
        outline=[0, 40, 80],
    )
    result = scan_pdf(path)
    assert result.candidate is True
    assert result.pages == 130
    assert result.signals["outline"] == 1.0
    assert result.signals["text_layer"] > 0.9
    assert result.score > 0.5


def test_a_real_one_page_pdf_is_not_a_candidate(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    path = _write_pdf(tmp_path / "receipt.pdf", ["TOTAL 4.00"])
    result = scan_pdf(path)
    assert result.candidate is False
    assert result.score == 0.0
