"""The labeling triage op end to end on faked seams (the ops-test pattern).

The handler's seams (vectors, labels, store probe) are patched, so the
numpy triage itself runs for real: a mislabeled unit in an otherwise
clean space is flagged first, clean units stay out of the queue, the
queue is deterministic, and every guard (no labels, thin corpus, a
class below the fold floor, absent tables, junk params) answers the
honest envelope.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest

from corpus_kb.analysis.vectors import VectorSpace
from corpus_kb.errors import ValidationError
from corpus_kb.tools import labeling_triage_tools

TENANT = "00000000-0000-0000-0000-000000000001"

# 8 "a" units near [1, 0], 5 clean "b" units near [0, 1], and one "b"
# unit sitting in the "a" cluster: the planted label error (U39). Both
# classes clear the default five-fold floor (8 and 6 members).
_CLEAN_A = [f"a{index:011d}" for index in range(8)]
_CLEAN_B = [f"b{index:011d}" for index in range(5)]
_OUTLIER = "m00000000000"


def _rows() -> tuple[VectorSpace, list[dict[str, object]]]:
    matrix = np.array(
        [[1.0, 0.0]] * 8 + [[0.0, 1.0]] * 5 + [[1.0, 0.0]],
        dtype=np.float64,
    )
    ids = _CLEAN_A + _CLEAN_B + [_OUTLIER]
    labels = ["a"] * 8 + ["b"] * 5 + ["b"]
    rows = [
        {"chunk_id": chunk_id, "label": label, "probability": 0.9}
        for chunk_id, label in zip(ids, labels, strict=True)
    ]
    return VectorSpace(ids=ids, matrix=matrix, excluded_zero_count=0), rows


def _singleton_world() -> tuple[VectorSpace, list[dict[str, object]]]:
    """12 units, 2 classes, ONE member in the max-index class.

    The exact fold-coverage crash shape: a stratified fold holds the
    singleton, its train split misses the class, and the module's shape
    check would raise a raw error without the handler's guard.
    """
    matrix = np.array([[1.0, 0.0]] * 11 + [[0.0, 1.0]], dtype=np.float64)
    ids = [f"s{index:011d}" for index in range(11)] + ["x00000000000"]
    labels = ["a"] * 11 + ["b"]
    rows = [
        {"chunk_id": chunk_id, "label": label, "probability": 0.9}
        for chunk_id, label in zip(ids, labels, strict=True)
    ]
    return VectorSpace(ids=ids, matrix=matrix, excluded_zero_count=0), rows


def _install(
    monkeypatch: pytest.MonkeyPatch,
    space: VectorSpace,
    rows: list[dict[str, object]],
    tables: bool = True,
) -> None:
    """Pin the three handler seams to the canned world."""

    async def vectors(pool: Any, tenant_id: str, config: dict[str, object] | None) -> VectorSpace:
        return space

    async def labels(pool: Any, tenant_id: str) -> list[dict[str, object]]:
        return rows

    async def present(pool: Any) -> bool:
        return tables

    monkeypatch.setattr(labeling_triage_tools, "_load_vectors", vectors)
    monkeypatch.setattr(labeling_triage_tools, "_load_labels", labels)
    monkeypatch.setattr(labeling_triage_tools, "_tables_present", present)


async def test_mislabeled_unit_flags_first_and_queue_stays_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    space, rows = _rows()
    _install(monkeypatch, space, rows)
    result = await labeling_triage_tools.labeling_triage(object(), tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["n_flagged"] == 1
    queue = result["queue"]
    assert len(queue) == 1
    row = queue[0]
    assert row["chunk_id"] == _OUTLIER
    assert row["assigned_label"] == "b"
    assert row["runner_up_label"] == "a"
    assert row["oos_probability"] < row["runner_up_probability"]
    assert result["classes"] == ["a", "b"]
    assert result["n_units"] == 14


async def test_queue_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    space, rows = _rows()
    _install(monkeypatch, space, rows)
    first = await labeling_triage_tools.labeling_triage(object(), tenant_id=TENANT)
    second = await labeling_triage_tools.labeling_triage(object(), tenant_id=TENANT)
    assert json.dumps(first["queue"]) == json.dumps(second["queue"])


async def test_limit_bounds_the_head_but_not_the_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    space, rows = _rows()
    _install(monkeypatch, space, rows)
    result = await labeling_triage_tools.labeling_triage(
        object(), tenant_id=TENANT, cv_folds=2, flag_threshold=0.99, limit=2
    )
    # The near-one threshold flags most units, so the honest total can
    # exceed the head: the queue is cut at 2 rows, the total never is.
    assert result["n_flagged"] >= 2
    assert len(result["queue"]) == 2
    assert result["n_flagged"] >= len(result["queue"])


async def test_thin_corpus_answers_insufficient_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    space, rows = _rows()
    _install(monkeypatch, space, rows[:5])
    result = await labeling_triage_tools.labeling_triage(object(), tenant_id=TENANT)
    assert result["status"] == "insufficient_data"
    assert result["n_flagged"] == 0
    assert result["queue"] == []
    assert result["remedy"]


async def test_singleton_class_answers_insufficient_data_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fold-coverage guard (regression): a singleton max-index class
    used to make the per-fold shape check raise a raw ValueError; the
    handler now answers the honest thin-class envelope instead."""
    space, rows = _singleton_world()
    _install(monkeypatch, space, rows)
    result = await labeling_triage_tools.labeling_triage(object(), tenant_id=TENANT, cv_folds=2)
    assert result["status"] == "insufficient_data"
    assert result["n_flagged"] == 0
    assert result["queue"] == []
    assert "'b'" in str(result["reason"])  # the thinnest class is named
    assert "holds only 1 of the needed 2" in str(result["reason"])
    assert result["classes"] == ["a", "b"]
    assert result["n_units"] == 12


async def test_no_labels_answers_insufficient_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    space, _rows_out = _rows()
    _install(monkeypatch, space, [])
    result = await labeling_triage_tools.labeling_triage(object(), tenant_id=TENANT)
    assert result["status"] == "insufficient_data"
    assert "no built labels" in str(result["reason"])


async def test_absent_tables_answer_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    space, rows = _rows()
    _install(monkeypatch, space, rows, tables=False)
    result = await labeling_triage_tools.labeling_triage(object(), tenant_id=TENANT)
    assert result["status"] == "unavailable"
    assert result["remedy"] == "run corpus-kb setup"


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"cv_folds": 1}, "cv_folds"),
        ({"cv_folds": True}, "cv_folds"),
        ({"flag_threshold": 0.0}, "flag_threshold"),
        ({"flag_threshold": 1.5}, "flag_threshold"),
        ({"limit": 0}, "limit"),
    ],
)
async def test_junk_params_raise_the_typed_error(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any], fragment: str
) -> None:
    space, rows = _rows()
    _install(monkeypatch, space, rows)
    with pytest.raises(ValidationError, match=fragment):
        await labeling_triage_tools.labeling_triage(object(), tenant_id=TENANT, **kwargs)


class _ProbeConn:
    """Tables present, canned reads keyed by table (the ops-probe conn)."""

    def __init__(self, label_rows: list[dict[str, Any]], vector_rows: list[dict[str, Any]]):
        self.label_rows = label_rows
        self.vector_rows = vector_rows

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "to_regclass" in sql:
            return "public.chunk_labels"
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM chunks_vectors" in sql:
            return list(self.vector_rows)
        if "FROM chunk_labels" in sql:
            return list(self.label_rows)
        return []

    def transaction(self) -> _ProbeConn:
        return self

    async def __aenter__(self) -> _ProbeConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ProbePool:
    """The pool shell tenant_connection needs (acquire + close)."""

    def __init__(self, conn: _ProbeConn) -> None:
        self._conn = conn

    def acquire(self) -> _ProbePool:
        return self

    async def __aenter__(self) -> _ProbeConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False

    async def close(self) -> None:
        return None


async def test_real_seams_over_a_probe_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """No seam is patched: the store probe, the label read, and the real
    vector loader all run against the canned pool and still flag the
    planted mislabel first."""
    space, rows = _rows()
    vector_rows = [
        {
            "chunk_id": chunk_id,
            "text": f"text {chunk_id}",
            "embedding": "[" + ",".join(str(float(v)) for v in row) + "]",
        }
        for chunk_id, row in zip(space.ids, space.matrix.tolist(), strict=True)
    ]
    label_rows = [
        {
            "chunk_id": str(row["chunk_id"]),
            "label": str(row["label"]),
            "probability": 0.9,
            "created_at": None,
        }
        for row in rows
    ]
    pool = _ProbePool(_ProbeConn(label_rows, vector_rows))
    result = await labeling_triage_tools.labeling_triage(pool, tenant_id=TENANT)
    assert result["status"] == "success"
    assert result["n_flagged"] == 1
    assert result["queue"][0]["chunk_id"] == _OUTLIER
    assert result["queue"][0]["assigned_label"] == "b"
