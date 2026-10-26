"""Survey strategy: CSV question/answer rows as context-carrying chunks.

Reads delimited Q/A data (one question column, one or more answer
columns) and emits one chunk per answer. Every chunk repeats the question
text in its metadata, so a retrieved answer always carries its question
as context — the survey twin of the transcript strategy's qa_context,
without the transcript-only metadata key.

Detection: a ``.csv`` suffix plus a header sniff for a question-like and
an answer-like column. A CSV without both column kinds never claims, so
plain tabular data keeps the default strategy. Speaker-header CSVs stay
with the transcript strategy: they carry neither column kind.

Spans: the canonical document text is the reconstructed ``Q:/A:`` block
list, and every chunk is an exact slice of it, so the offset invariant
``document_text[start:end] == chunk text`` holds by construction.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ..base import (
    TOKEN_BUDGET_CHARS,
    ChunkPlan,
    DetectResult,
    PlannedChunk,
    SourceProfile,
)

SURVEY_STRATEGY_NAME = "survey"

# Column-name vocabulary (normalized: lower-case, stripped). The first
# match wins for the question; every match becomes an answer column.
_QUESTION_COLUMNS = frozenset({"question", "question_text", "prompt", "q"})
_ANSWER_COLUMNS = frozenset({"answer", "answer_text", "response", "response_text", "a"})

_SEPARATOR = "\n\n"
_CSV_SUFFIX_SCORE = 0.3
_QA_HEADER_SCORE = 0.5
_HINT_SCORE = 0.2


def _norm(header: object) -> str:
    """Lower-case, stripped header cell text."""
    return header.strip().lower() if isinstance(header, str) else ""


def _find_columns(headers: list[str]) -> tuple[str | None, list[str]]:
    """The question column and the answer columns in a header row."""
    question = next((h for h in headers if _norm(h) in _QUESTION_COLUMNS), None)
    answers = [h for h in headers if h != question and _norm(h) in _ANSWER_COLUMNS]
    return question, answers


def _header_cells(head: str) -> list[str]:
    """The first CSV line's cells (empty when the head has no line)."""
    lines = head.splitlines()
    if not lines:
        return []
    return next(csv.reader(io.StringIO(lines[0])), [])


def _exact_slices(text: str, budget: int) -> list[str]:
    """Cut ``text`` into exact slices, each within ``budget`` chars."""
    return [text[offset : offset + budget] for offset in range(0, len(text), budget)]


def _line_span(text: str, start: int, end: int) -> tuple[int, int]:
    """1-based line numbers for a char span (the unstructured_chunker rule)."""
    return text.count("\n", 0, start) + 1, text.count("\n", 0, end) + 1


class SurveyStrategy:
    """Question/answer chunking for CSV survey exports."""

    name = SURVEY_STRATEGY_NAME

    def detect(self, source_type_hint: str | None, filename: str, head: str) -> DetectResult:
        """Recognize Q/A-shaped CSVs from suffix + header — no full parse."""
        name = (filename or "").lower()
        if not name.endswith(".csv"):
            return DetectResult(0.0, "no survey signal")
        question, answers = _find_columns(_header_cells(head))
        confidence = _CSV_SUFFIX_SCORE
        reason = "csv-suffix"
        if question is not None and answers:
            confidence += _QA_HEADER_SCORE
            reason = "csv-qa-header"
        if source_type_hint == SURVEY_STRATEGY_NAME:
            confidence += _HINT_SCORE
        return DetectResult(min(confidence, 1.0), reason)

    def build_chunks(self, raw: str, filename: str, profile: SourceProfile) -> ChunkPlan:
        """Parse the CSV and emit one budget-bounded chunk per answer."""
        warnings: list[str] = []
        text = self._source_text(raw, profile)
        question_col, answer_cols, units = _parse_units(text)
        if question_col is None or not answer_cols:
            return ChunkPlan(
                strategy=self.name,
                empty_reason="no question and answer columns found in the header",
                warnings=warnings,
            )
        if not units:
            return ChunkPlan(
                strategy=self.name,
                document_text="",
                empty_reason="no question/answer rows recovered from source",
                warnings=warnings,
            )
        document_text = _SEPARATOR.join(
            f"Q: {question}\nA: {answer}" for question, _, answer, _ in units
        )
        chunks: list[PlannedChunk] = []
        offset = 0
        for question, column, answer, row_num in units:
            block = f"Q: {question}\nA: {answer}"
            pieces = (
                _exact_slices(block, TOKEN_BUDGET_CHARS)
                if len(block) > TOKEN_BUDGET_CHARS
                else [block]
            )
            if len(pieces) > 1:
                warnings.append(f"row {row_num} answer exceeds the token budget; split")
            for piece in pieces:
                start, end = offset, offset + len(piece)
                start_line, end_line = _line_span(document_text, start, end)
                chunks.append(
                    PlannedChunk(
                        text=piece,
                        index=len(chunks),
                        metadata={
                            "strategy": SURVEY_STRATEGY_NAME,
                            "question": question,
                            "row": row_num,
                            "answer_column": column,
                        },
                        source_type="text",
                        start_line=start_line,
                        end_line=end_line,
                        source_start_char=start,
                        source_end_char=end,
                    )
                )
                offset += len(piece)
            offset += len(_SEPARATOR)
        return ChunkPlan(
            strategy=self.name,
            chunks=chunks,
            document_text=document_text,
            warnings=warnings,
        )

    def _source_text(self, raw: str, profile: SourceProfile) -> str:
        """Files are read from disk (raw may be empty); raw text parses as is."""
        if profile.is_file:
            path = Path(profile.path)
            if path.exists():
                return path.read_text(encoding="utf-8-sig", errors="replace")
        return raw


def _parse_units(
    text: str,
) -> tuple[str | None, list[str], list[tuple[str, str, str, int]]]:
    """Question column, answer columns, and the non-empty (q, col, a, row) units."""
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames) if reader.fieldnames is not None else []
    question_col, answer_cols = _find_columns(headers)
    if question_col is None or not answer_cols:
        return question_col, answer_cols, []
    units: list[tuple[str, str, str, int]] = []
    for row_num, row in enumerate(reader, start=2):
        question = (row.get(question_col) or "").strip()
        cells = [(c, (row.get(c) or "").strip()) for c in answer_cols]
        answers = [(c, a) for c, a in cells if a]
        if not question or not answers:
            continue
        units.extend((question, column, answer, row_num) for column, answer in answers)
    return question_col, answer_cols, units
