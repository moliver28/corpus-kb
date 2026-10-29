"""The textbook candidacy scanner (textbook-ingestion program, Wave 2C).

A pypdf-only port of the candidacy scanner: no sklearn, no external
service. One PDF answers one score in [0, 1] — the weighted sum of eight
deterministic signals (weights in ``textbook_signals.SCAN_WEIGHTS``).

Method: the page count must sit in the band (120-1500) to count as a
candidate. The scanner reads 12 evenly spaced pages, then measures the
signals with fixed regexes and a token-set Jaccard across neighbors
(``textbook_signals`` owns the measurement). Same bytes in, same score
out.

Calibration references from a real run (sanity, not pins): a 680-page
history volume with bookmarks scored 0.40; a 912-page handbook without
bookmarks scored 0.20; a one-page receipt scored 0.00 (below the band).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from corpus_kb.errors import DependencyMissingError, ValidationError
from corpus_kb.ingestion.textbook_signals import (
    SCAN_WEIGHTS,
    measure_signals,
    zero_signals,
)

if TYPE_CHECKING:
    from pypdf import PdfReader

__all__ = [
    "BAND_MAX_PAGES",
    "BAND_MIN_PAGES",
    "SAMPLE_PAGES",
    "SCAN_WEIGHTS",
    "ScanResult",
    "scan_path",
    "scan_pdf",
]

SAMPLE_PAGES = 12
BAND_MIN_PAGES = 120
BAND_MAX_PAGES = 1500


class _ReaderFactory(Protocol):
    """The one seam tests fake (a live PdfReader needs real bytes)."""

    def __call__(self, path: Path) -> PdfReader: ...


def _reader_for(path: Path) -> PdfReader:
    """Open one PDF with pypdf, lazy-imported; missing extra reads typed."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DependencyMissingError(
            "pypdf is not installed. The scan needs it.",
            remediation="Install the [scan] extra: pip install '.[scan]'. Then run again.",
        ) from exc
    return PdfReader(str(path))


@dataclass(frozen=True)
class ScanResult:
    """One PDF's candidacy: pages, score, and the signal breakdown."""

    path: str
    pages: int = 0
    score: float = 0.0
    candidate: bool = False
    signals: dict[str, float] = field(default_factory=zero_signals)
    detail: str | None = None


def scan_path(
    path: str | Path, *, reader_factory: _ReaderFactory = _reader_for
) -> list[ScanResult]:
    """Scan every recursive *.pdf under path; score desc, then path asc."""
    root = Path(path)
    if not root.exists():
        raise ValidationError(
            f"The scan path does not exist: {root}",
            remediation="Pass a PDF file or a directory that holds PDFs.",
        )
    results = [scan_pdf(pdf, reader_factory=reader_factory) for pdf in _pdf_files(root)]
    results.sort(key=lambda r: (-r.score, r.path))
    return results


def scan_pdf(path: str | Path, *, reader_factory: _ReaderFactory = _reader_for) -> ScanResult:
    """One PDF's candidacy score; an unreadable file scores 0.0 with a note."""
    try:
        reader = reader_factory(Path(path))
        pages = len(reader.pages)
    except DependencyMissingError:
        raise
    except Exception as exc:
        return ScanResult(
            path=str(path),
            detail=f"unreadable: {exc.__class__.__name__}",
            signals=zero_signals(),
        )
    if pages < BAND_MIN_PAGES or pages > BAND_MAX_PAGES:
        return ScanResult(
            path=str(path),
            pages=pages,
            detail=f"outside the band ({BAND_MIN_PAGES}-{BAND_MAX_PAGES} pages)",
            signals=zero_signals(),
        )
    texts = _sample_texts(reader, pages)
    outline_items = _outline_count(getattr(reader, "outline", []) or [])
    signals = measure_signals(texts, outline_items)
    score = round(sum(SCAN_WEIGHTS[name] * value for name, value in signals.items()), 3)
    return ScanResult(path=str(path), pages=pages, score=score, candidate=True, signals=signals)


def _sample_indices(pages: int, count: int = SAMPLE_PAGES) -> list[int]:
    """Evenly spaced page indices; a short book answers every page."""
    if pages <= count:
        return list(range(pages))
    return sorted({round(i * (pages - 1) / (count - 1)) for i in range(count)})


def _sample_texts(reader: PdfReader, pages: int) -> list[str]:
    texts: list[str] = []
    for index in _sample_indices(pages):
        try:
            texts.append(reader.pages[index].extract_text() or "")
        except Exception:
            texts.append("")
    return texts


def _outline_count(outline: object) -> int:
    """Count destinations through the nested reader.outline walk."""
    if not isinstance(outline, list):
        return 0
    count = 0
    for item in outline:
        count += _outline_count(item) if isinstance(item, list) else 1
    return count


def _pdf_files(root: Path) -> Iterator[Path]:
    """A file is itself; a directory walks recursively for *.pdf."""
    if root.is_file():
        yield root
        return
    if root.is_dir():
        for child in sorted(root.rglob("*")):
            if child.is_file() and child.suffix.lower() == ".pdf":
                yield child
