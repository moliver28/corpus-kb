"""The golden-set format: labeled queries for offline evaluation.

One JSON object per line (JSONL). Every record carries its query plus
EXACTLY ONE label kind:

    {"query": str, "relevant_chunk_ids": [uuid-str, ...], "answer": str | null}
    {"query": str, "relevant_spans": [{"doc": str, "start": int, "end": int}, ...]}
    {"query": str, "relevant_units": [int, ...]}

Span labels stay stable when chunk boundaries move, so chunkers compare
fairly (Wave 7). Unit labels name research units directly. Two sources
feed the format:

- ``corpus-kb eval build-golden --from-assignments`` derives records from
  human-reviewed codings (``research_assignments.status IN
  ('confirmed','overridden')`` — the U45 label rule, v8 ground rule 9:
  numbers are never fabricated from unreviewed data). A unit's retrieval
  chunk id is the DETERMINISTIC child UUID ``research/retrieval.py``
  mints, so a golden record and a search hit compare exactly.
- hand-authored files label any corpus: paste real chunk ids from
  ``corpus-kb search --json`` output.

Validation is strict and honest: every malformed line names its line
number and the specific problem. No record is silently dropped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from uuid import UUID

from corpus_kb.errors import ValidationError

__all__ = [
    "GoldenFormatError",
    "GoldenRecord",
    "SpanLabel",
    "build_golden_from_assignments",
    "golden_digest",
    "golden_summary",
    "load_golden",
]

# The exact key set one golden record may carry (strict: typos are errors,
# not silent no-ops).
GOLDEN_KEYS = frozenset(
    {"query", "relevant_chunk_ids", "relevant_spans", "relevant_units", "answer"}
)
_LABEL_KINDS = ("relevant_chunk_ids", "relevant_spans", "relevant_units")


class GoldenFormatError(ValidationError):
    """A golden-set line is not a valid record."""

    remediation = "Fix the named line. Keep one JSON object per line."


@dataclass(frozen=True)
class SpanLabel:
    """One labeled char span inside one document (doc id or source label)."""

    doc: str
    start: int
    end: int


@dataclass(frozen=True)
class GoldenRecord:
    """One labeled query: text, the relevant labels, an ideal answer.

    Exactly one label kind is non-empty; the loader enforces it.
    """

    query: str
    relevant_chunk_ids: tuple[str, ...]
    answer: str | None = None
    relevant_spans: tuple[SpanLabel, ...] = ()
    relevant_units: tuple[int, ...] = ()


def load_golden(path: Path) -> list[GoldenRecord]:
    """Parse one JSONL golden file; every bad line raises with its number.

    Line numbers are 1-based and count every physical line, so an editor
    and this loader agree on where the problem is.
    """
    records: list[GoldenRecord] = []
    text = path.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        records.append(_parse_line(line, number))
    return records


def _parse_line(line: str, number: int) -> GoldenRecord:
    parsed: object
    try:
        parsed = json.loads(line)
    except JSONDecodeError as exc:
        raise GoldenFormatError(f"golden line {number}: not valid JSON ({exc.msg})") from None
    if not isinstance(parsed, dict):
        raise GoldenFormatError(f"golden line {number}: one JSON object per line, got other JSON")
    row = {str(key): value for key, value in parsed.items()}
    unknown = sorted(set(row) - GOLDEN_KEYS)
    if unknown:
        raise GoldenFormatError(
            f"golden line {number}: unknown key(s) {', '.join(unknown)}; "
            f"allowed: {', '.join(sorted(GOLDEN_KEYS))}"
        )
    present = [kind for kind in _LABEL_KINDS if kind in row]
    if len(present) != 1:
        raise GoldenFormatError(
            f"golden line {number}: exactly one label kind is required"
            f" ({', '.join(_LABEL_KINDS)}); got {len(present)}"
        )
    kind = present[0]
    spans = _spans(row, number) if kind == "relevant_spans" else ()
    units = _units(row, number) if kind == "relevant_units" else ()
    chunk_ids = _chunk_ids(row, number) if kind == "relevant_chunk_ids" else ()
    return GoldenRecord(
        query=_query(row, number),
        relevant_chunk_ids=chunk_ids,
        answer=_answer(row, number),
        relevant_spans=spans,
        relevant_units=units,
    )


def _query(row: dict[str, object], number: int) -> str:
    query = row.get("query")
    if not isinstance(query, str) or not query.strip():
        raise GoldenFormatError(f"golden line {number}: 'query' must be a non-empty string")
    return query


def _chunk_ids(row: dict[str, object], number: int) -> tuple[str, ...]:
    raw = row.get("relevant_chunk_ids")
    if not isinstance(raw, list) or not raw:
        raise GoldenFormatError(
            f"golden line {number}: 'relevant_chunk_ids' must be a non-empty list of chunk ids"
        )
    ids: list[str] = []
    for item in raw:
        candidate = str(item)
        try:
            ids.append(str(UUID(candidate)))
        except ValueError:
            raise GoldenFormatError(
                f"golden line {number}: chunk id {candidate!r} is not a UUID"
            ) from None
    return tuple(ids)


def _answer(row: dict[str, object], number: int) -> str | None:
    answer = row.get("answer")
    if answer is None:
        return None
    if not isinstance(answer, str) or not answer.strip():
        raise GoldenFormatError(f"golden line {number}: 'answer' must be a string or null")
    return answer


def _spans(row: dict[str, object], number: int) -> tuple[SpanLabel, ...]:
    raw = row.get("relevant_spans")
    if not isinstance(raw, list) or not raw:
        raise GoldenFormatError(
            f"golden line {number}: 'relevant_spans' must be a non-empty list of span objects"
        )
    spans: list[SpanLabel] = []
    for item in raw:
        if not isinstance(item, dict):
            raise GoldenFormatError(
                f"golden line {number}: every span must be an object with doc/start/end"
            )
        doc = item.get("doc")
        start = item.get("start")
        end = item.get("end")
        if not isinstance(doc, str) or not doc.strip():
            raise GoldenFormatError(f"golden line {number}: every span needs a non-empty 'doc'")
        if not isinstance(start, int) or isinstance(start, bool):
            raise GoldenFormatError(f"golden line {number}: every span needs an integer 'start'")
        if not isinstance(end, int) or isinstance(end, bool):
            raise GoldenFormatError(f"golden line {number}: every span needs an integer 'end'")
        if start < 0 or end <= start:
            raise GoldenFormatError(
                f"golden line {number}: span [{start}, {end}) is empty or reversed"
            )
        spans.append(SpanLabel(doc=doc, start=start, end=end))
    return tuple(spans)


def _units(row: dict[str, object], number: int) -> tuple[int, ...]:
    raw = row.get("relevant_units")
    if not isinstance(raw, list) or not raw:
        raise GoldenFormatError(
            f"golden line {number}: 'relevant_units' must be a non-empty list of unit ids"
        )
    units: list[int] = []
    for item in raw:
        if not isinstance(item, int) or isinstance(item, bool) or item <= 0:
            raise GoldenFormatError(
                f"golden line {number}: unit id {item!r} must be a positive integer"
            )
        units.append(item)
    return tuple(units)


def golden_digest(path: Path) -> str:
    """SHA-256 over the canonical JSON of every record, in file order.

    Key order and whitespace never move the digest, so two hand-edits of
    the same labels hash equal and the calibration lock stays stable.
    """
    records = load_golden(path)
    payload = [
        {
            "query": record.query,
            "answer": record.answer,
            "relevant_chunk_ids": list(record.relevant_chunk_ids),
            "relevant_spans": [
                {"doc": span.doc, "start": span.start, "end": span.end}
                for span in record.relevant_spans
            ],
            "relevant_units": list(record.relevant_units),
        }
        for record in records
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_golden_from_assignments(rows: Iterable[Mapping[str, object]]) -> list[GoldenRecord]:
    """Build records from reviewed-assignment query rows.

    One row per code (the shape ``load_reviewed_labels`` fetches):
    ``name``, ``brief_definition`` and ``unit_ids`` (the human-reviewed
    unit ids). The query text is the code name plus its brief definition;
    each unit maps to its deterministic retrieval-chunk UUID, exactly the
    id a research search hit carries. Rows with no reviewed units are
    skipped: an unlabeled query cannot score, and dropping it beats
    reporting a fake 0.0.
    """
    from corpus_kb.research.retrieval import _child_uuid

    records: list[GoldenRecord] = []
    for row in rows:
        raw_units = row.get("unit_ids")
        units: list[object] = list(raw_units) if isinstance(raw_units, list) else []
        # array_agg of bigint yields Python ints; anything else in the row
        # is not a unit id and cannot label a chunk.
        labeled = [item for item in units if isinstance(item, int)]
        chunk_ids = tuple(_child_uuid(f"unit:{unit_id}") for unit_id in labeled)
        if not chunk_ids:
            continue
        records.append(
            GoldenRecord(
                query=f"{row.get('name')}. {row.get('brief_definition')}",
                relevant_chunk_ids=chunk_ids,
                answer=None,
            )
        )
    return records


def golden_summary(records: list[GoldenRecord]) -> dict[str, int]:
    """Counts for the report envelope: records and the answer split."""
    with_answer = sum(1 for record in records if record.answer is not None)
    return {
        "records": len(records),
        "with_answer": with_answer,
        "without_answer": len(records) - with_answer,
    }
