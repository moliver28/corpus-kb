"""Seed-file parsing, validation, and resolution (WP-3).

Every bad seed shape is a ``ValueError`` naming the first problem, so the
handler can answer an honest ``insufficient_labels`` envelope without
re-reading the file. Resolution maps seed ids onto stored vectors and
names up to five missing ids with the remediation text.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from corpus_kb.classification import seeds
from corpus_kb.classification.engine import StoredVector


def _uuid(i: int) -> str:
    return f"00000000-0000-0000-0000-{i:012d}"


def _write(tmp_path: Path, payload: object) -> str:
    path = tmp_path / "seeds.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _vectors(ids: list[str]) -> list[StoredVector]:
    return [
        StoredVector(chunk_id=cid, text=f"text {cid}", vector=np.array([1.0, 0.0], np.float32))
        for cid in ids
    ]


def test_parse_keeps_canonical_sorted_pairs(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {"chunk_id": _uuid(2).upper(), "label": "beta"},
            {"chunk_id": _uuid(1), "label": "alpha"},
        ],
    )
    seed_set = seeds.parse_seed_file(path)
    assert seed_set.pairs == [(_uuid(1), "alpha"), (_uuid(2), "beta")]
    assert seed_set.classes == ["alpha", "beta"]
    assert seed_set.class_counts == {"alpha": 1, "beta": 1}


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    missing = str(tmp_path / "absent.json")
    with pytest.raises(ValueError, match=r"absent\.json"):
        seeds.parse_seed_file(missing)


def test_invalid_json_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        seeds.parse_seed_file(str(path))


def test_empty_or_non_list_payload_is_rejected(tmp_path: Path) -> None:
    for payload in ([], {"chunk_id": "x", "label": "y"}):
        path = _write(tmp_path, payload)
        with pytest.raises(ValueError, match="empty or not a JSON list"):
            seeds.parse_seed_file(path)


def test_duplicate_chunk_ids_are_listed(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {"chunk_id": _uuid(1), "label": "alpha"},
            {"chunk_id": _uuid(1), "label": "beta"},
        ],
    )
    with pytest.raises(ValueError, match=r"repeats chunk ids") as excinfo:
        seeds.parse_seed_file(path)
    assert _uuid(1) in str(excinfo.value)


def test_non_uuid_chunk_ids_are_listed(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"chunk_id": "not-a-uuid", "label": "alpha"}])
    with pytest.raises(ValueError, match="not uuids"):
        seeds.parse_seed_file(path)


def test_missing_label_names_the_entry(tmp_path: Path) -> None:
    path = _write(tmp_path, [{"chunk_id": _uuid(1), "label": "   "}])
    with pytest.raises(ValueError, match="entry 0"):
        seeds.parse_seed_file(path)


def test_non_object_entry_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, ["alpha"])
    with pytest.raises(ValueError, match="entry 0 is not an object"):
        seeds.parse_seed_file(path)


def test_resolve_maps_pairs_to_stored_vectors(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [
            {"chunk_id": _uuid(2), "label": "beta"},
            {"chunk_id": _uuid(1), "label": "alpha"},
        ],
    )
    stored = _vectors([_uuid(1), _uuid(2)])
    matrix, labels = seeds.resolve(seeds.parse_seed_file(path), stored)
    assert matrix.dtype == np.float32
    assert matrix.shape == (2, 2)
    assert labels.tolist() == ["alpha", "beta"]
    assert np.array_equal(matrix[0], stored[0].vector)


def test_resolve_names_up_to_five_missing_ids(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        [{"chunk_id": _uuid(i), "label": "alpha"} for i in range(7)],
    )
    stored = _vectors([_uuid(99)])
    with pytest.raises(ValueError) as excinfo:
        seeds.resolve(seeds.parse_seed_file(path), stored)
    message = str(excinfo.value)
    assert "7 seed chunk ids" in message
    assert message.count(_uuid(0)) == 1
    assert "Re-ingest the seed documents" in message
