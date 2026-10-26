"""Markdown heading segmenter: heading-boundary sections with char spans.

Segments RAW markdown text (ingest-text bodies) at ATX (``#``) and setext
(underline) heading boundaries. Each section becomes one chunk within the
token budget, and every chunk stores its exact char span on the canonical
document text, so the offset invariant ``text[start:end] == chunk text``
holds by construction. A preamble before the first heading is its own
section; every chunk carries the heading breadcrumb in its metadata.

Markdown FILES keep today's path on purpose: the unstructured partition
already yields per-element chunks with heading paths, so detection never
claims a ``.md``/``.markdown``/``.rst`` file and the default envelope
stays byte-identical. The segmenter only takes over where the current
path is degenerate — raw text, which today collapses to one chunk.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..base import (
    TOKEN_BUDGET_CHARS,
    ChunkPlan,
    DetectResult,
    PlannedChunk,
    SourceProfile,
)
from .ast_chunks import CODE_SUFFIXES

MARKDOWN_SECTIONS_STRATEGY_NAME = "markdown_sections"

# Files that keep the default partition (byte-identical envelopes) and
# files where "# " lines are comments, not headings.
_KEEP_DEFAULT_SUFFIXES = (".md", ".markdown", ".mkd", ".mdown", ".rst")

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.*?\S)\s*$")
_SETEXT_UNDERLINE = re.compile(r"^(=+|-+)\s*$")
_FENCE = re.compile(r"^(```|~~~)")
_ATX_SCORE = 0.55
_SETEXT_SCORE = 0.5
_HINT_SCORE = 0.2


def _exact_slices(text: str, budget: int) -> list[str]:
    """Cut ``text`` into exact slices, each within ``budget`` chars."""
    return [text[offset : offset + budget] for offset in range(0, len(text), budget)]


def _line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """1-based line numbers for a char span (the unstructured_chunker rule)."""
    return text.count("\n", 0, start) + 1, text.count("\n", 0, end) + 1


def _setext_level(previous: str, underline: str) -> int | None:
    """The setext level when ``underline`` closes ``previous`` as a heading."""
    if not previous.strip() or _ATX_HEADING.match(previous.strip()):
        return None
    match = _SETEXT_UNDERLINE.match(underline.strip())
    if match is None:
        return None
    return 1 if match.group(1).startswith("=") else 2


def _has_setext(text: str) -> bool:
    """True when any non-empty line closes as a setext heading."""
    lines = text.splitlines()
    return any(
        _setext_level(lines[i - 1], line) is not None for i, line in enumerate(lines) if i > 0
    )


def _sections(text: str) -> list[tuple[int, int, int, list[str]]]:
    """Sections as (start, end, level, heading_path) spans on ``text``.

    The path lists ancestor titles plus the section's own title; the
    preamble (text before the first heading) is a level-0 section with an
    empty path. Headings inside fenced code blocks never split.
    """
    lines = text.splitlines(keepends=True)
    offsets = [0] * (len(lines) + 1)
    for i, line in enumerate(lines):
        offsets[i + 1] = offsets[i] + len(line)
    sections: list[tuple[int, int, int, list[str]]] = []
    stack: list[tuple[int, str]] = []
    section_start = 0
    section_level = 0
    section_path: list[str] = []
    fenced = False
    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if _FENCE.match(stripped.lstrip()):
            fenced = not fenced
            continue
        if fenced:
            continue
        level: int | None = None
        title = ""
        boundary = offsets[i]
        atx = _ATX_HEADING.match(stripped)
        if atx is not None:
            level = len(atx.group(1))
            title = atx.group(2)
        elif i > 0:
            setext = _setext_level(lines[i - 1].rstrip("\r\n"), stripped)
            if setext is not None:
                level = setext
                title = lines[i - 1].rstrip("\r\n").strip()
                boundary = offsets[i - 1]
        if level is None:
            continue
        if boundary > section_start:
            sections.append((section_start, boundary, section_level, section_path))
        stack[:] = [(lvl, name) for lvl, name in stack if lvl < level]
        stack.append((level, title))
        section_start = boundary
        section_level = level
        section_path = [name for _, name in stack]
    sections.append((section_start, offsets[-1], section_level, section_path))
    return sections


class MarkdownSectionsStrategy:
    """Heading-boundary segmentation for raw markdown text."""

    name = MARKDOWN_SECTIONS_STRATEGY_NAME

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        """Claim only raw text with real heading structure (never .md files)."""
        name = (filename or "").lower()
        if name.endswith(_KEEP_DEFAULT_SUFFIXES) or name.endswith(tuple(sorted(CODE_SUFFIXES))):
            return DetectResult(0.0, "no markdown signal")
        if any(_ATX_HEADING.match(line) for line in head.splitlines()):
            confidence = _ATX_SCORE
            reason = "atx-headings"
        elif _has_setext(head):
            confidence = _SETEXT_SCORE
            reason = "setext-headings"
        else:
            return DetectResult(0.0, "no markdown signal")
        if source_type_hint == "markdown":
            confidence += _HINT_SCORE
        return DetectResult(min(confidence, 1.0), reason)

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        """One budget-bounded chunk per heading section, spans exact."""
        warnings: list[str] = []
        text = self._source_text(raw, profile)
        if not text.strip():
            return ChunkPlan(
                strategy=self.name,
                document_text=text,
                empty_reason="no content to segment",
                warnings=warnings,
            )
        spans = [span for span in _sections(text) if span[2] > 0 or span[0] < span[1]]
        if not any(level > 0 for _, _, level, _ in spans):
            return ChunkPlan(
                strategy=self.name,
                document_text=text,
                empty_reason="no heading structure found",
                warnings=warnings,
            )
        chunks: list[PlannedChunk] = []
        for index, (start, end, level, path) in enumerate(spans):
            block = text[start:end]
            pieces = (
                _exact_slices(block, TOKEN_BUDGET_CHARS)
                if len(block) > TOKEN_BUDGET_CHARS
                else [block]
            )
            if len(pieces) > 1:
                warnings.append(f"section {index} exceeds the token budget; split")
            for piece_index, piece in enumerate(pieces):
                piece_start = start + piece_index * TOKEN_BUDGET_CHARS
                piece_end = piece_start + len(piece)
                start_line, end_line = _line_span(text, piece_start, piece_end)
                chunks.append(
                    PlannedChunk(
                        text=piece,
                        index=len(chunks),
                        metadata={
                            "strategy": MARKDOWN_SECTIONS_STRATEGY_NAME,
                            "section": index,
                            "heading_level": level,
                            "heading_path": list(path),
                        },
                        source_type="markdown",
                        start_line=start_line,
                        end_line=end_line,
                        source_start_char=piece_start,
                        source_end_char=piece_end,
                    )
                )
        return ChunkPlan(
            strategy=self.name,
            chunks=chunks,
            document_text=text,
            warnings=warnings,
        )

    def _source_text(self, raw: str, profile: SourceProfile) -> str:
        """Files are read from disk (raw may be empty); raw text parses as is."""
        if profile.is_file:
            path = Path(profile.path)
            if path.exists():
                return path.read_text(encoding="utf-8-sig", errors="replace")
        return raw
