"""The golden-set format: labeled queries for offline evaluation.

One JSON object per line (JSONL):

    {"query": str, "relevant_chunk_ids": [uuid-str, ...], "answer": str | null}

Two sources feed the format:

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
    "build_golden_from_assignments",
    "golden_summary",
    "load_golden",
]

# The exact key set one golden record may carry (strict: typos are errors,
# not silent no-ops).
GOLDEN_KEYS = frozenset({"query", "relevant_chunk_ids", "answer"})


class GoldenFormatError(ValidationError):
    """A golden-set line is not a valid record."""

    remediation = "Fix the named line. Keep one JSON object per line."


@dataclass(frozen=True)
class GoldenRecord:
    """One labeled query: text, the relevant chunk ids, an ideal answer."""

    query: str
    relevant_chunk_ids: tuple[str, ...]
    answer: str | None


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
    return GoldenRecord(
        query=_query(row, number),
        relevant_chunk_ids=_chunk_ids(row, number),
        answer=_answer(row, number),
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
