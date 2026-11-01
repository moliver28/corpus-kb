"""The labeling gold-check op end to end on faked seams (ops-test pattern).

The handler's seams (labels, store probe) are patched and the gold dev
set rides a tmp file, so the PPI math itself runs for real: per-class
rows carry the raw agreement plus the corrected prevalence, a thin gold
set answers honestly inside a success envelope, and every guard (file
missing, unreadable JSON, no overlap, no labels, absent tables, junk
params) answers the honest envelope or the typed error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.tools import labeling_gold_tools

TENANT = "00000000-0000-0000-0000-000000000001"


def _label_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(10):
        label = "reports" if index < 6 else "meetings"
        rows.append(
            {
                "chunk_id": f"{index + 1:08d}-0000-0000-0000-000000000000",
                "label": label,
                "probability": 0.9,
            }
        )
    return rows


def _install(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, object]],
    tables: bool = True,
) -> None:
    """Pin the two handler seams to the canned world."""

    async def labels(pool: Any, tenant_id: str) -> list[dict[str, object]]:
        return rows

    async def present(pool: Any) -> bool:
        return tables

    monkeypatch.setattr(labeling_gold_tools, "_load_labels", labels)
    monkeypatch.setattr(labeling_gold_tools, "_tables_present", present)


def _gold_file(tmp_path: Path, gold: dict[str, str]) -> str:
    path = tmp_path / "gold.json"
    path.write_text(json.dumps(gold), encoding="utf-8")
    return str(path)


def _gold(rows: list[dict[str, object]], overrides: dict[str, str] | None = None) -> dict[str, str]:
    """A random-looking gold draw over the first five built chunks."""
    gold = {str(row["chunk_id"]): str(row["label"]) for row in rows[:5]}
    if overrides:
        gold.update(overrides)
    return gold


async def test_gold_agreement_and_correction_land_per_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = _label_rows()
    _install(monkeypatch, rows)
    # One planted mismatch: the model says reports, gold says meetings.
    gold_path = _gold_file(tmp_path, _gold(rows, {rows[0]["chunk_id"]: "meetings"}))
    result = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=gold_path
    )
    assert result["status"] == "success"
    assert result["class_count"] == 2
    by_class = {row["class_name"]: row for row in result["validations"]}
    # raw_accuracy is the label agreement over the matched gold set, so
    # it is class-independent: 4 of the 5 gold chunks agree (the planted
    # mismatch is the one disagreement) no matter which class is scored.
    assert by_class["reports"]["raw_accuracy"] == pytest.approx(0.8)
    assert by_class["meetings"]["raw_accuracy"] == pytest.approx(0.8)
    assert by_class["reports"]["n_gold"] == 5
    for row in result["validations"]:
        assert set(row) == {
            "class_name",
            "status",
            "label",
            "raw_accuracy",
            "estimate",
            "lower",
            "upper",
            "n_population",
            "n_gold",
            "effective_audit_n",
        }
    params = result["params"]
    assert params["gold_matched"] == 5
    assert params["n_population"] == 10


async def test_off_build_gold_ids_are_ignored_and_counted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = _label_rows()
    _install(monkeypatch, rows)
    gold_path = _gold_file(tmp_path, {**_gold(rows), "not-a-built-chunk": "reports"})
    result = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=gold_path
    )
    assert result["params"]["gold_matched"] == 5
    assert result["params"]["gold_ignored"] == 1


async def test_thin_gold_set_answers_honestly_inside_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = _label_rows()
    _install(monkeypatch, rows)
    only = next(iter(_gold(rows)))
    gold_path = _gold_file(tmp_path, {only: "reports"})
    result = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=gold_path
    )
    # The op ran; the PPI layer is the one that refuses to correct on one
    # audited item (honest unknown, never a made-up interval).
    assert result["status"] == "success"
    row = result["validations"][0]
    assert row["status"] == "insufficient_data"
    assert row["label"] == "uncorrected"
    assert row["lower"] is None and row["upper"] is None


async def test_no_overlap_answers_insufficient_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = _label_rows()
    _install(monkeypatch, rows)
    gold_path = _gold_file(tmp_path, {"not-a-built-chunk": "reports"})
    result = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=gold_path
    )
    assert result["status"] == "insufficient_data"
    assert result["remedy"]


async def test_unknown_label_names_are_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rows = _label_rows()
    _install(monkeypatch, rows)
    gold_path = _gold_file(tmp_path, {rows[0]["chunk_id"]: "not-a-vocabulary-label"})
    result = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=gold_path
    )
    assert result["status"] == "insufficient_data"


async def test_missing_or_broken_gold_files_answer_insufficient_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _label_rows())
    missing = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=str(tmp_path / "nope.json")
    )
    assert missing["status"] == "insufficient_data"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    unreadable = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=str(broken)
    )
    assert unreadable["status"] == "insufficient_data"
    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    not_object = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=str(empty)
    )
    assert not_object["status"] == "insufficient_data"


async def test_no_built_labels_answers_insufficient_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, [])
    gold_path = _gold_file(tmp_path, {"any-chunk": "reports"})
    result = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=gold_path
    )
    assert result["status"] == "insufficient_data"
    assert "no built labels" in str(result["reason"])


async def test_absent_tables_answer_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _label_rows(), tables=False)
    gold_path = _gold_file(tmp_path, {"any-chunk": "reports"})
    result = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=gold_path
    )
    assert result["status"] == "unavailable"
    assert result["remedy"] == "run corpus-kb setup"


async def test_missing_gold_file_argument_raises_the_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _label_rows())
    with pytest.raises(ValidationError, match="gold_file"):
        await labeling_gold_tools.labeling_gold_check(object(), tenant_id=TENANT)


@pytest.mark.parametrize("level", [0.0, 1.0, 1.5])
async def test_junk_confidence_levels_raise_the_typed_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, level: float
) -> None:
    _install(monkeypatch, _label_rows())
    gold_path = _gold_file(tmp_path, {"any-chunk": "reports"})
    with pytest.raises(ValidationError, match="confidence_level"):
        await labeling_gold_tools.labeling_gold_check(
            object(), tenant_id=TENANT, gold_file=gold_path, confidence_level=level
        )


def test_short_sha_answers_empty_on_an_unreadable_path(tmp_path: Path) -> None:
    assert labeling_gold_tools._short_sha(str(tmp_path)) == ""
    assert labeling_gold_tools._short_sha(str(tmp_path / "gold.json")).startswith("")


async def test_bad_gold_label_values_answer_insufficient_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _label_rows())
    bad_label = tmp_path / "bad-label.json"
    bad_label.write_text(json.dumps({"any-chunk": ""}), encoding="utf-8")
    result = await labeling_gold_tools.labeling_gold_check(
        object(), tenant_id=TENANT, gold_file=str(bad_label)
    )
    assert result["status"] == "insufficient_data"
    assert "non-empty label name" in str(result["reason"])


class _ProbeConn:
    """Tables present, canned chunk_labels read (the ops-probe conn)."""

    def __init__(self, label_rows: list[dict[str, Any]]) -> None:
        self.label_rows = label_rows

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "to_regclass" in sql:
            return "public.chunk_labels"
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
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


async def test_real_seams_over_a_probe_pool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No seam is patched: the store probe and the label read run against
    the canned pool, and the gold check lands the per-class rows."""
    rows = _label_rows()
    label_rows = [
        {
            "chunk_id": str(row["chunk_id"]),
            "label": str(row["label"]),
            "probability": 0.9,
            "created_at": None,
        }
        for row in rows
    ]
    pool = _ProbePool(_ProbeConn(label_rows))
    # One meetings entry so BOTH classes validate (the first five built
    # chunks are all reports).
    gold = {**_gold(rows), str(rows[6]["chunk_id"]): str(rows[6]["label"])}
    gold_path = _gold_file(tmp_path, gold)
    result = await labeling_gold_tools.labeling_gold_check(
        pool, tenant_id=TENANT, gold_file=gold_path
    )
    assert result["status"] == "success"
    assert result["class_count"] == 2
    assert result["params"]["n_population"] == 10
