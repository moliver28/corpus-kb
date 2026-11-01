"""The per-book runner behind the textbook bench CLI (program W0).

This module owns the only PDF-facing code in the bench: it opens the
book, parses it through the production window seam — the same
``window_ranges`` / cache / ``parse_window_*`` functions the strategy
drives, with the cache honored and the min_pages gate skipped — builds
the independent text layer, and scores the parse with the pure metrics
from ``textbook_bench``. The strategy class itself is not imported on
purpose: it drags the default strategy's unstructured stack in, and the
docling probe environment must stay parser-isolated (issue #220). The
docling converter loads in-process, because the bench runs under the
interpreter that has docling (spawn would pick the same one).

The text layer prefers pypdfium2 (imported lazily; it reaches this tree
only as a transitive dependency of the ``[docling]`` extra) and falls
back to pypdf, which shares its extractor with the pypdf parser and so
is only semi-independent there. The report's layer name records which
extractor ran.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any

from corpus_kb.eval.textbook_bench import (
    _PAGE_JOIN,
    attribution_report,
    chunk_report,
    page_fidelity,
    rebuild_pages,
    section_f1,
)
from corpus_kb.ingestion.base import TOKEN_BUDGET_CHARS
from corpus_kb.ingestion.strategies.textbook_assemble import (
    assign_sections,
    flatten_windows,
    plan_chunks,
    section_blocks,
)
from corpus_kb.ingestion.strategies.textbook_cache import (
    cache_file,
    file_sha256,
    load_window_cache,
    save_window_cache,
    window_ranges,
)
from corpus_kb.ingestion.strategies.textbook_pages import (
    PARSER_VERSIONS,
    OutlineBookmark,
    PageItem,
    WindowRecord,
    hush_pdf_logs,
    load_docling_converter,
    open_pdf,
    parse_window_docling,
    parse_window_pypdf,
    read_outline,
)

__all__ = ["run_book", "text_layer"]

_MISSING_DOCLING = "textbook bench needs the optional 'docling' extra; used pypdf page text"


def _load_pdfium() -> Any | None:
    """The pypdfium2 module when importable, else None (lazy import)."""
    try:
        return importlib.import_module("pypdfium2")
    except ImportError:
        return None


def text_layer(path: Path, reader: Any, page_count: int) -> tuple[dict[int, str], str]:
    """Per-page text from an independent extractor, plus the layer name."""
    module = _load_pdfium()
    if module is not None:
        try:
            doc = module.PdfDocument(str(path))
            try:
                texts = {
                    index + 1: doc[index].get_textpage().get_text_range()
                    for index in range(page_count)
                }
            finally:
                doc.close()
            return texts, "pypdfium2"
        except Exception:
            pass  # a pdfium failure degrades to the pypdf layer, never a crash
    with hush_pdf_logs():
        texts = {index + 1: reader.pages[index].extract_text() or "" for index in range(page_count)}
    return texts, "pypdf-fallback"


def _parse_windows(
    path: Path,
    reader: Any,
    digest: str,
    page_count: int,
    parser: str,
    cache_dir: Path,
    window_pages: int,
    warnings: list[str],
) -> list[WindowRecord]:
    """The production window loop with the parser pinned.

    Cache hits skip, a parsed window caches before it appends, and a
    docling window that cannot parse (extra missing, convert crash)
    falls back to pypdf and caches under that parser — the same rule
    ``TextbookStrategy`` applies, minus the subprocess isolation.
    """
    records: list[WindowRecord] = []
    converter: Any | None = None
    docling_checked = False
    docling_active = parser == "docling"
    for index, (start, end) in enumerate(window_ranges(page_count, window_pages)):
        active = "docling" if docling_active else "pypdf"
        cached = load_window_cache(
            cache_file(cache_dir, active, digest, index), PARSER_VERSIONS[active], digest, index
        )
        if cached is not None:
            records.append(cached)
            continue
        if docling_active and not docling_checked:
            docling_checked = True
            converter = load_docling_converter()
            if converter is None:
                docling_active = False
                warnings.append(_MISSING_DOCLING)
        items: list[PageItem] | None = None
        effective = "docling" if docling_active else "pypdf"
        if docling_active:
            try:
                items = parse_window_docling(converter, path, start, end)
            except Exception:
                warnings.append(f"docling failed on pages {start} to {end}; used pypdf page text")
                items = None
                effective = "pypdf"
        if items is None:
            items = parse_window_pypdf(reader, start, end, warnings)
            effective = "pypdf"
        record = WindowRecord(start_page=start, end_page=end, items=items)
        save_window_cache(
            cache_file(cache_dir, effective, digest, index),
            PARSER_VERSIONS[effective],
            digest,
            index,
            record,
        )
        records.append(record)
    return records


def run_book(
    path: Path, parser: str, cache_dir: Path, window_pages: int = 50, max_pages: int = 0
) -> dict[str, object]:
    """Parse one book through the production window seam and score it.

    ``max_pages`` caps the parsed span for a timeboxed parser run; the
    gold bookmarks shrink to the same span, so a capped run stays a
    fair section score over the pages it actually parsed.
    """
    reader, note = open_pdf(path)
    if reader is None or note is not None:
        return {"error": f"the pdf did not open: {note}", "file": str(path)}
    page_count = len(reader.pages)
    parsed_span = min(page_count, max_pages) if max_pages > 0 else page_count
    bookmarks: list[OutlineBookmark] = [
        bookmark for bookmark in read_outline(reader) if bookmark.page <= parsed_span
    ]
    digest = file_sha256(path)
    warnings: list[str] = []
    started = time.perf_counter()
    records = _parse_windows(
        path, reader, digest, parsed_span, parser, cache_dir, window_pages, warnings
    )
    parse_seconds = round(time.perf_counter() - started, 3)
    items = flatten_windows(records)
    texts: dict[int, list[str]] = {}
    for item in items:
        if item.text.strip():
            texts.setdefault(item.page, []).append(item.text)
    parsed_texts = {page: _PAGE_JOIN.join(parts) for page, parts in texts.items()}
    layer, layer_name = text_layer(path, reader, parsed_span)
    blocks = section_blocks(assign_sections(items, bookmarks))
    document_text, chunks = plan_chunks(blocks, "textbook", TOKEN_BUDGET_CHARS, warnings)
    rebuilt, spans = rebuild_pages(items)
    return {
        "attribution": attribution_report(chunks, spans, rebuilt == document_text),
        "chunks": chunk_report(chunks, TOKEN_BUDGET_CHARS),
        "fidelity": page_fidelity(parsed_texts, layer),
        "file": str(path),
        "page_count": page_count,
        "parsed_pages": parsed_span,
        "parse_seconds": parse_seconds,
        "parser": parser,
        "sections": section_f1(
            [block.title for block in blocks if block.title],
            [bookmark.title for bookmark in bookmarks],
        ),
        "sha256": digest,
        "text_layer": layer_name,
        "warnings": warnings[:10],
        "window_pages": window_pages,
    }
