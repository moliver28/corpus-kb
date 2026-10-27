"""Span-anchored and unit-anchored golden labels (unified-ingestion Wave 7).

The old ``relevant_chunk_ids`` format keeps working unchanged. The two new
label kinds let one golden file compare chunkers fairly: spans stay stable
when chunk boundaries move, and unit ids stay stable when the research path
re-derives chunk ids.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_kb.eval.golden import GoldenFormatError, golden_digest, load_golden


def _write(tmp_path: Path, rows: list[dict[str, object]], name: str = "golden.jsonl") -> Path:
    path = tmp_path / name
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def test_chunk_id_format_unchanged(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [{"query": "q", "relevant_chunk_ids": ["00000000-0000-0000-0000-000000000001"]}],
    )
    records = load_golden(path)
    assert len(records) == 1
    assert records[0].relevant_chunk_ids == ("00000000-0000-0000-0000-000000000001",)
    assert records[0].relevant_spans == ()
    assert records[0].relevant_units == ()


def test_span_labels_parse(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "query": "grid plan",
                "relevant_spans": [
                    {"doc": "sym://a", "start": 0, "end": 120},
                    {"doc": "sym://a", "start": 500, "end": 640},
                ],
            }
        ],
    )
    records = load_golden(path)
    spans = records[0].relevant_spans
    assert len(spans) == 2
    assert spans[0].doc == "sym://a"
    assert spans[0].start == 0
    assert spans[0].end == 120


def test_unit_labels_parse(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"query": "q", "relevant_units": [3, 17]}])
    records = load_golden(path)
    assert records[0].relevant_units == (3, 17)


def test_two_label_kinds_on_one_line_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {
                "query": "q",
                "relevant_units": [1],
                "relevant_chunk_ids": ["00000000-0000-0000-0000-000000000001"],
            }
        ],
    )
    with pytest.raises(GoldenFormatError, match="line 1"):
        load_golden(path)


def test_bad_span_shape_names_the_line(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"query": "q", "relevant_spans": [{"doc": "d", "start": 5}]}])
    with pytest.raises(GoldenFormatError, match=r"line 1.*'end'"):
        load_golden(path)


def test_reversed_span_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path, [{"query": "q", "relevant_spans": [{"doc": "d", "start": 9, "end": 2}]}]
    )
    with pytest.raises(GoldenFormatError, match="line 1"):
        load_golden(path)


def test_empty_label_list_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"query": "q", "relevant_units": []}])
    with pytest.raises(GoldenFormatError, match="line 1"):
        load_golden(path)


def test_zero_unit_label_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"query": "q", "relevant_units": [0]}])
    with pytest.raises(GoldenFormatError, match="line 1"):
        load_golden(path)


def test_digest_is_canonical(tmp_path: Path) -> None:
    row = {"query": "q", "relevant_units": [3, 17]}
    first = _write(tmp_path, [row], "a.jsonl")
    reordered = {"relevant_units": [3, 17], "query": "q"}
    second = _write(tmp_path, [reordered], "b.jsonl")
    assert golden_digest(first) == golden_digest(second)


def test_digest_changes_with_content(tmp_path: Path) -> None:
    first = _write(tmp_path, [{"query": "q", "relevant_units": [3]}], "a.jsonl")
    second = _write(tmp_path, [{"query": "different", "relevant_units": [3]}], "b.jsonl")
    assert golden_digest(first) != golden_digest(second)
