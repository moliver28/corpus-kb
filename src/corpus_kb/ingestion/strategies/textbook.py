"""Textbook strategy: windowed parsing for large PDFs.

The gate claims PDFs at or above ``textbook.min_pages`` pages (the pypdf
probe). Parsing runs in windows of ``textbook.window_pages`` pages — one
``DocumentConverter.convert`` call per window — and each parsed window
caches on disk under ``textbook.cache_dir``, keyed by parser version,
file sha256, and window index, so retries and resumes skip parsed
windows.

Detection note (honest): the registry's ``detect()`` sees only the file
name and the text head, never the file, so the page-count gate cannot
run there. ``detect`` claims the ``.pdf`` suffix; ``build_chunks`` runs
the real page gate and falls back to the default plan — with a recorded
warning — below ``min_pages``.

Seam rule: when a window starts mid-section, the previous window's open
section title carries, and content repeated across a boundary dedupes
by page (first window wins). Bookmarks are the only global structure a
windowed parse sees (docling discussion #2744); they only fill pages
where labels are thin.

Degrade paths, never a crash: without the ``[docling]`` extra the
strategy warns once and parses windows with pypdf only; without the
``[scan]`` extra, or below ``min_pages``, it falls back to the default
plan. Docling can crash the process on scanned pages, so each Docling
window parses in a spawned child when ``window_subprocess`` is set; the
parent only probes the module spec, never imports docling. A crashed,
timed-out, or wrapper-failed window takes the pypdf path (issue #219).
The window cache evicts its oldest files past ``cache_max_mb``. Every
parsed window leaves a :class:`WindowOutcome` (parser, cache hit,
warnings) whose fold becomes the conditional ``textbook`` envelope key.

The docling seams live in ``textbook_windows.py``; the assembly in
``textbook_assemble.py``; the cache in ``textbook_cache.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..base import (
    TOKEN_BUDGET_CHARS,
    ChunkPlan,
    DetectResult,
    SourceProfile,
)
from .default import DefaultStrategy
from .textbook_assemble import (
    assign_sections,
    flatten_windows,
    plan_chunks,
    section_blocks,
)
from .textbook_cache import (
    CacheKey,
    file_sha256,
    load_window_cache,
    prune_window_cache,
    save_window_cache,
    window_ranges,
)
from .textbook_docling import parser_key
from .textbook_outcomes import WindowOutcome, summarize
from .textbook_pages import (
    PARSER_VERSIONS,
    PageItem,
    WindowRecord,
    open_pdf,
    parse_window_pypdf,
    read_outline,
)
from .textbook_settings import TextbookSettings, settings_from
from .textbook_windows import confirm_docling, parse_one_docling_window

TEXTBOOK_STRATEGY_NAME = "textbook"

_PDF_SUFFIX_SCORE = 0.55
_HINT_SCORE = 0.2


class TextbookStrategy:
    """Windowed, cached, structure-aware chunking for large PDFs."""

    name = TEXTBOOK_STRATEGY_NAME

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        """Claim PDF file names; the page gate refines at build time."""
        del head
        if not (filename or "").lower().endswith(".pdf"):
            return DetectResult(0.0, "no pdf signal")
        confidence = _PDF_SUFFIX_SCORE
        if source_type_hint == TEXTBOOK_STRATEGY_NAME:
            confidence += _HINT_SCORE
        return DetectResult(min(confidence, 1.0), "pdf-suffix")

    def _empty(
        self, reason: str, warnings: list[str], summary: dict[str, object] | None = None
    ) -> ChunkPlan:
        """An honest empty plan (non-file sources, missing files, no text)."""
        return ChunkPlan(
            strategy=self.name, empty_reason=reason, warnings=warnings, textbook_summary=summary
        )

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        """Gate on page count, parse in cached windows, assemble chunks."""
        warnings: list[str] = []
        if not profile.is_file:
            return self._empty("textbook parsing requires a file source", warnings)
        path = Path(profile.path)
        if not path.exists():
            return self._empty("the source file does not exist", warnings)
        settings = settings_from(profile.config)
        reader, note = open_pdf(path)
        if reader is None or note is not None:
            return self._fallback(
                raw, filename, profile, warnings, f"textbook gate: {note}; used default chunking"
            )
        page_count = len(reader.pages)
        if page_count < settings.min_pages:
            note = (
                f"textbook gate: the PDF has {page_count} pages, below "
                f"the {settings.min_pages} page minimum; used default chunking"
            )
            return self._fallback(raw, filename, profile, warnings, note)
        bookmarks = read_outline(reader)
        digest = file_sha256(path)
        outcomes = self._parse_windows(path, reader, digest, page_count, settings, warnings)
        items = flatten_windows([outcome.record for outcome in outcomes])
        if not items:
            return self._empty("no text recovered from the pdf", warnings, summarize(outcomes))
        blocks = section_blocks(assign_sections(items, bookmarks))
        document_text, chunks = plan_chunks(
            blocks, TEXTBOOK_STRATEGY_NAME, TOKEN_BUDGET_CHARS, warnings
        )
        return ChunkPlan(
            strategy=self.name,
            chunks=chunks,
            document_text=document_text,
            warnings=warnings,
            textbook_summary=summarize(outcomes),
        )

    def _parse_windows(
        self,
        path: Path,
        reader: Any,
        digest: str,
        page_count: int,
        settings: TextbookSettings,
        warnings: list[str],
    ) -> list[WindowOutcome]:
        """Parse every window, cache writes after each, cache hits skip.

        In-process mode loads the converter only when a window needs it;
        subprocess mode only probes the module spec. The parser falls
        back per book (missing extra, one warning) or per window (a
        failed convert call, one warning each). A degraded window caches
        under its parser, so a later run with the extra re-parses it.
        Each window yields one outcome: its record, the parser that
        served it, whether the cache did, and that window's warnings.
        """
        structured = settings.structured_output
        parser_name = settings.parser if settings.parser in PARSER_VERSIONS else "docling"
        if settings.parser not in PARSER_VERSIONS:
            warnings.append(f"unknown textbook.parser '{settings.parser}'; used docling")
        cache_dir = Path(settings.cache_dir).expanduser()
        if settings.cache_max_mb > 0:
            prune_window_cache(cache_dir, settings.cache_max_mb)
        outcomes: list[WindowOutcome] = []
        converter: Any | None = None
        extra_checked = False
        docling_active = parser_name == "docling"
        for index, (start, end) in enumerate(window_ranges(page_count, settings.window_pages)):
            active = "docling" if docling_active else "pypdf"
            cached = self._cached_record(cache_dir, digest, index, active, start, end, structured)
            if cached is not None:
                outcomes.append(cached)
                continue
            if docling_active and not extra_checked:
                extra_checked = True
                docling_active, converter = confirm_docling(settings, converter, warnings)
            use_docling = docling_active and (settings.window_subprocess or converter is not None)
            effective = "docling" if use_docling else "pypdf"
            if effective != active:
                cached = self._cached_record(
                    cache_dir, digest, index, effective, start, end, structured
                )
                if cached is not None:
                    outcomes.append(cached)
                    continue
            window_warnings: list[str] = []
            items: list[PageItem] | None = None
            if use_docling:
                items, effective = parse_one_docling_window(
                    settings, converter, path, start, end, index, window_warnings
                )
            if items is None:
                items = parse_window_pypdf(reader, start, end, window_warnings)
            warnings.extend(window_warnings)
            record = WindowRecord(start_page=start, end_page=end, items=items)
            key = CacheKey(parser_key(effective, structured), digest, index)
            save_window_cache(key.path(cache_dir), key, record)
            outcomes.append(WindowOutcome(record, effective, False, tuple(window_warnings)))
        return outcomes

    def _cached_record(
        self,
        cache_dir: Path,
        digest: str,
        index: int,
        parser: str,
        start: int,
        end: int,
        structured: bool = False,
    ) -> WindowOutcome | None:
        """The cached window for one parser, or None on a miss.

        The entry carries the ``parser_key`` tag, so a flag-on run reads
        and writes the rich variant's own files; the outcome still names
        the plain parser.
        """
        key = CacheKey(parser_key(parser, structured), digest, index)
        record = load_window_cache(key.path(cache_dir), key, start, end)
        if record is None:
            return None
        return WindowOutcome(record, parser, True, ())

    def _fallback(
        self,
        raw: str,
        filename: str,
        profile: SourceProfile,
        warnings: list[str],
        note: str,
    ) -> ChunkPlan:
        """The default strategy's plan under this strategy's name, with a note."""
        plan = DefaultStrategy().build_chunks(raw, filename, profile)
        plan.strategy = TEXTBOOK_STRATEGY_NAME
        plan.warnings = [*plan.warnings, note]
        return plan
