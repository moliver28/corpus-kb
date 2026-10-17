"""The golden-set format tests (offline): loader, builder, summary.

The loader must be strict with HONEST errors — every malformed line
names its line number. The builder maps reviewed-assignment rows onto
the deterministic unit-child chunk UUIDs research search returns, so a
golden id and a hit id compare exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb.eval.golden import (
    GoldenFormatError,
    GoldenRecord,
    build_golden_from_assignments,
    golden_summary,
    load_golden,
)
from corpus_kb.research.retrieval import _child_uuid


def _write(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "golden.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_load_golden_happy_path(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            '{"query": "startup order", "relevant_chunk_ids":'
            ' ["00000000-0000-0000-0000-000000000001"], "answer": "api first"}',
            '{"query": "retry policy", "relevant_chunk_ids":'
            ' ["00000000-0000-0000-0000-000000000002"], "answer": null}',
        ],
    )
    records = load_golden(path)
    assert len(records) == 2
    assert records[0] == GoldenRecord(
        query="startup order",
        relevant_chunk_ids=("00000000-0000-0000-0000-000000000001",),
        answer="api first",
    )
    assert records[1].answer is None
    # Blank lines are skipped, not errors.
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert len(load_golden(path)) == 2


@pytest.mark.parametrize(
    ("line", "needle"),
    [
        ("{not json", "not valid JSON"),
        ("[1, 2]", "one JSON object per line"),
        ('{"relevant_chunk_ids": ["00000000-0000-0000-0000-000000000001"]}', "'query'"),
        (
            '{"query": "q", "relevant_chunk_ids": ["00000000-0000-0000-0000-000000000001"],'
            ' "answer": "a", "weight": 3}',
            "unknown key",
        ),
        (
            '{"query": "q", "relevant_chunk_ids": ["not-a-uuid"]}',
            "not a UUID",
        ),
        ('{"query": "q", "relevant_chunk_ids": []}', "non-empty list"),
        (
            '{"query": "q", "relevant_chunk_ids": ["00000000-0000-0000-0000-000000000001"],'
            ' "answer": 42}',
            "must be a string or null",
        ),
    ],
)
def test_load_golden_malformed_lines_name_the_line(tmp_path: Path, line: str, needle: str) -> None:
    ok = '{"query": "ok", "relevant_chunk_ids": ["00000000-0000-0000-0000-000000000001"]}'
    path = _write(tmp_path, [ok, line])
    with pytest.raises(GoldenFormatError) as excinfo:
        load_golden(path)
    message = str(excinfo.value)
    # The malformed line is the SECOND physical line, and the error says so.
    assert message.startswith("golden line 2:")
    assert needle in message


def test_builder_maps_units_to_deterministic_child_uuids() -> None:
    rows = [
        {
            "code_id": "c1",
            "name": "Billing Concern",
            "brief_definition": "Mentions invoicing or payments.",
            "unit_ids": [11, 12],
        },
        {
            "code_id": "c2",
            "name": "Empty",
            "brief_definition": "No reviewed units.",
            "unit_ids": [],
        },
    ]
    records = build_golden_from_assignments(rows)
    assert len(records) == 1  # the unlabeled code is skipped, not scored as 0.0
    expected = (_child_uuid("unit:11"), _child_uuid("unit:12"))
    assert records[0].relevant_chunk_ids == expected
    assert records[0].query == "Billing Concern. Mentions invoicing or payments."
    assert records[0].answer is None


def test_builder_tolerates_null_unit_entries() -> None:
    rows = [
        {
            "name": "A",
            "brief_definition": "B.",
            "unit_ids": [7, None],
        }
    ]
    records = build_golden_from_assignments(rows)
    assert records[0].relevant_chunk_ids == (_child_uuid("unit:7"),)


def test_golden_summary_counts_answers() -> None:
    records = [
        GoldenRecord("a", ("00000000-0000-0000-0000-000000000001",), "x"),
        GoldenRecord("b", ("00000000-0000-0000-0000-000000000002",), None),
        GoldenRecord("c", ("00000000-0000-0000-0000-000000000003",), None),
    ]
    assert golden_summary(records) == {"records": 3, "with_answer": 1, "without_answer": 2}
    assert golden_summary([]) == {"records": 0, "with_answer": 0, "without_answer": 0}
