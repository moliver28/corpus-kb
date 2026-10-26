"""Code strategy: tree-sitter AST chunking behind the optional ``code`` extra.

Parses a source file with the grammar picked from its suffix and chunks
top-level definitions (functions, classes, statements), packing adjacent
small siblings up to the token budget. Every chunk is an exact slice of
the canonical text, so char spans stay truthful; oversized definitions
recurse one level into their children, then fall back to exact slices.

When the extra is missing, the grammar is unknown, or the text carries no
parseable code structure, the strategy falls back to the default
strategy's plan and records a warnings entry — the degradation is honest
(the semchunk pattern), never silent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..base import (
    TOKEN_BUDGET_CHARS,
    ChunkPlan,
    DetectResult,
    PlannedChunk,
    SourceProfile,
)
from .ast_chunks import (
    CODE_SUFFIXES,
    byte_table,
    char_offset,
    grammar_for,
    group_spans,
    load_parser,
    named_children,
)
from .default import DefaultStrategy

CODE_STRATEGY_NAME = "code"

_CODE_SUFFIX_SCORE = 0.55
_HINT_SCORE = 0.2
_MAX_RECURSION_DEPTH = 4


def _exact_slices(text: str, budget: int) -> list[str]:
    """Cut ``text`` into exact slices, each within ``budget`` chars."""
    return [text[offset : offset + budget] for offset in range(0, len(text), budget)]


def _line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """1-based line numbers for a char span (the unstructured_chunker rule)."""
    return text.count("\n", 0, start) + 1, text.count("\n", 0, end) + 1


def _slice_spans(
    start: int, end: int, node_type: str, warnings: list[str]
) -> list[tuple[int, int, str]]:
    """Exact budget slices across one oversized span (the last resort)."""
    warnings.append("one definition exceeds the token budget; hard-split")
    return [
        (offset, min(offset + TOKEN_BUDGET_CHARS, end), node_type)
        for offset in range(start, end, TOKEN_BUDGET_CHARS)
    ]


class CodeStrategy:
    """Grammar-aware chunking for source files (optional ``code`` extra)."""

    name = CODE_STRATEGY_NAME

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        """Claim known code suffixes; content sniffing stays out (no guessing)."""
        name = filename or ""
        if name.lower().endswith(tuple(sorted(CODE_SUFFIXES))):
            confidence = _CODE_SUFFIX_SCORE
            reason = "code-suffix"
        elif source_type_hint == CODE_STRATEGY_NAME:
            return DetectResult(_HINT_SCORE, "source-type-hint")
        else:
            return DetectResult(0.0, "no code signal")
        if source_type_hint == CODE_STRATEGY_NAME:
            confidence += _HINT_SCORE
        return DetectResult(min(confidence, 1.0), reason)

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        """AST-chunk the file, or fall back to the default plan with a note."""
        warnings: list[str] = []
        grammar = grammar_for(filename)
        if grammar is None:
            return self._fallback(
                raw,
                filename,
                profile,
                warnings,
                "code strategy: no tree-sitter grammar for this file; used default chunking",
            )
        parser = load_parser(grammar[1], grammar[2])
        if parser is None:
            return self._fallback(
                raw,
                filename,
                profile,
                warnings,
                "code strategy needs the optional 'code' extra; used default chunking",
            )
        text = self._source_text(raw, profile)
        if not text:
            return ChunkPlan(
                strategy=self.name,
                document_text=text,
                empty_reason="no source text",
                warnings=warnings,
            )
        pieces = self._ast_pieces(parser, text, warnings)
        if not pieces:
            return ChunkPlan(
                strategy=self.name,
                document_text=text,
                empty_reason="no top-level code structure found",
                warnings=warnings,
            )
        chunks: list[PlannedChunk] = []
        for start, end, node_type in pieces:
            start_line, end_line = _line_span(text, start, end)
            chunks.append(
                PlannedChunk(
                    text=text[start:end],
                    index=len(chunks),
                    metadata={
                        "strategy": CODE_STRATEGY_NAME,
                        "language": grammar[0],
                        "node_type": node_type,
                    },
                    source_type="code",
                    start_line=start_line,
                    end_line=end_line,
                    source_start_char=start,
                    source_end_char=end,
                )
            )
        return ChunkPlan(
            strategy=self.name,
            chunks=chunks,
            document_text=text,
            warnings=warnings,
        )

    def _ast_pieces(
        self, parser: Any, text: str, warnings: list[str]
    ) -> list[tuple[int, int, str]]:
        """Char spans for every budget-bounded piece of the parsed tree."""
        data = text.encode("utf-8")
        table = byte_table(text)
        root = parser.parse(data).root_node
        return self._node_spans(root, table, warnings, depth=0)

    def _node_spans(
        self,
        node: Any,
        table: list[int],
        warnings: list[str],
        depth: int,
    ) -> list[tuple[int, int, str]]:
        """Budget-bounded char spans from one node's children (recursive)."""
        out: list[tuple[int, int, str]] = []
        spans: list[tuple[int, int, Any]] = []
        skipped = 0
        for child in named_children(node):
            start = char_offset(table, child.start_byte)
            end = char_offset(table, child.end_byte)
            if start == -1 or end == -1 or start >= end:
                skipped += 1
                continue
            spans.append((start, end, child))
        if skipped:
            warnings.append("some nodes had unaligned byte offsets; they were skipped")
        for group_start, group_end, nodes, oversized in group_spans(
            spans, TOKEN_BUDGET_CHARS, end_limit=char_offset(table, node.end_byte)
        ):
            child_node = nodes[0]
            if not oversized:
                out.append((group_start, group_end, str(child_node.type)))
            elif depth < _MAX_RECURSION_DEPTH and named_children(child_node):
                out.extend(self._node_spans(child_node, table, warnings, depth + 1))
            else:
                out.extend(_slice_spans(group_start, group_end, str(child_node.type), warnings))
        return out

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
        plan.strategy = CODE_STRATEGY_NAME
        plan.warnings = [*plan.warnings, note]
        return plan

    def _source_text(self, raw: str, profile: SourceProfile) -> str:
        """Files are read from disk (raw may be empty); raw text parses as is."""
        if profile.is_file:
            path = Path(profile.path)
            if path.exists():
                data = path.read_bytes()
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    return data.decode("latin-1")
        return raw
