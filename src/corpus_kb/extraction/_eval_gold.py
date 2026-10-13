"""Gold JSONL loading for the NER eval harness.

Files are UTF-8 JSONL, one document per line:
``{doc_id, text, spans: [{start, end, label}]}`` with half-open
character offsets. Loading validates shape strictly — a malformed gold
file is an error, never a silently shrunk eval set.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SpanKey = tuple[str, int, int, str]


@dataclass(frozen=True, slots=True)
class GoldSpan:
    """One hand-labeled mention span."""

    start: int
    end: int
    label: str


@dataclass(frozen=True, slots=True)
class GoldDoc:
    """One gold-labeled document."""

    doc_id: str
    text: str
    spans: tuple[GoldSpan, ...]


def load_gold(path: str | Path) -> list[GoldDoc]:
    """Load gold JSONL, validating shape and offsets.

    Raises:
        ValueError: On malformed lines, out-of-range offsets, or
            duplicate doc_ids.
    """
    docs: list[GoldDoc] = []
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"gold line {line_number}: expected a JSON object")
            doc_id, text, spans_raw = raw.get("doc_id"), raw.get("text"), raw.get("spans", [])
            if not isinstance(doc_id, str) or not isinstance(text, str):
                raise ValueError(f"gold line {line_number}: doc_id and text must be strings")
            if doc_id in seen:
                raise ValueError(f"gold line {line_number}: duplicate doc_id {doc_id!r}")
            seen.add(doc_id)
            if not isinstance(spans_raw, list):
                raise ValueError(f"gold line {line_number}: spans must be a list")
            spans = [_parse_span(span_raw, line_number, len(text)) for span_raw in spans_raw]
            docs.append(GoldDoc(doc_id=doc_id, text=text, spans=tuple(spans)))
    return docs


def _parse_span(span_raw: object, line_number: int, text_length: int) -> GoldSpan:
    """Validate one gold span entry against the text length."""
    if not isinstance(span_raw, dict):
        raise ValueError(f"gold line {line_number}: span must be an object")
    start = _span_int(span_raw.get("start"), "start", line_number)
    end = _span_int(span_raw.get("end"), "end", line_number)
    label = span_raw.get("label")
    if not isinstance(label, str) or not label:
        raise ValueError(f"gold line {line_number}: span label must be a string")
    if not 0 <= start < end <= text_length:
        raise ValueError(
            f"gold line {line_number}: span [{start},{end}) out of range "
            f"for text of length {text_length}"
        )
    return GoldSpan(start=start, end=end, label=label)


def _span_int(value: object, name: str, line_number: int) -> int:
    """Require a non-bool int field on a gold span."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"gold line {line_number}: span {name} must be an int")
    return value
