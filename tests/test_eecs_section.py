"""The EECS section: paired predictions vs gold labels, error math, isolation.

The section builder lives in ``matrix_tools`` (next to the math and the
paired-row reader); ``analyze_run`` only gates and appends it. The
reader runs against a scripted connection keyed by SQL substring —
no Postgres. Covered: the fixed section position (after
classification, before trends), the no-row-when-off contract that keeps
the legacy four-section envelope intact, the honest
unavailable/skipped guards, failure isolation, and the reader's
newest-set pick.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb.analysis import report as report_mod
from corpus_kb.errors import DatabaseUnavailableError
from corpus_kb.tools import analysis_tools, matrix_tools

TENANT = "00000000-0000-0000-0000-000000000001"

EECS_ON = {"analysis": {"eecs": {"enabled": True}}}
CLASSIFICATION_ON = {"analysis": {"classification": {"enabled": True}}}


class _ProbePool:
    """The patched handlers never touch it; any use fails loudly."""

    def __getattr__(self, name: str) -> Any:
        def _nope(*args: object, **kwargs: object) -> object:
            raise AssertionError(f"the pool method {name} must not run in this test")

        return _nope


# ---------------------------------------------------------------------------
# The pure evaluation wrapper
# ---------------------------------------------------------------------------


def test_evaluate_returns_matrix_and_per_class_rows() -> None:
    result = matrix_tools.evaluate(["spam", "ham"], ["spam", "spam"])
    assert result["labels"] == ["ham", "spam"]
    # rows = truth, columns = predicted: ham->spam and spam->spam.
    assert result["matrix"] == [[0, 1], [0, 1]]
    assert {row["label"] for row in result["per_class"]} == {"ham", "spam"}


# ---------------------------------------------------------------------------
# The paired-row reader (scripted connection, SQL-keyed)
# ---------------------------------------------------------------------------


class _ScriptedConn:
    """Answers fetch calls by SQL substring; to_regclass probes truthy."""

    def __init__(self, results: dict[str, list[dict[str, Any]]]) -> None:
        self._results = results

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        for key, rows in self._results.items():
            if key in sql:
                return rows
        return []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return "table"

    async def execute(self, sql: str, *args: Any) -> str:
        return "OK"

    def transaction(self) -> _ScriptedConn:
        return self

    async def __aenter__(self) -> _ScriptedConn:
        return self

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ScriptedPool:
    def __init__(self, conn: _ScriptedConn) -> None:
        self._conn = conn

    def acquire(self) -> _ScriptedPool:
        return self

    async def __aenter__(self) -> _ScriptedConn:
        return self._conn

    async def __aexit__(self, *args: Any) -> bool:
        return False


def _paired_results() -> dict[str, list[dict[str, Any]]]:
    return {
        "FROM label_sets": [{"set_id": "set-1"}],
        "FROM chunk_predictions": [
            {"chunk_id": "c1", "label": "spam"},
            {"chunk_id": "c2", "label": "ham"},
        ],
        "FROM chunk_labels": [
            {"chunk_id": "c1", "label": "spam"},
            {"chunk_id": "c2", "label": "spam"},
            {"chunk_id": "c3", "label": "ham"},
        ],
    }


async def test_reader_pairs_predictions_with_gold_labels() -> None:
    pool = _ScriptedPool(_ScriptedConn(_paired_results()))
    paired, gold_count, prediction_count, set_id = await matrix_tools.read_paired_rows(pool, TENANT)
    assert paired == [("c1", "spam", "spam"), ("c2", "spam", "ham")]
    assert gold_count == 3
    assert prediction_count == 2
    assert set_id == "set-1"


async def test_reader_orders_pairs_by_chunk_id() -> None:
    results = _paired_results()
    results["from chunk_predictions"] = [
        {"chunk_id": "c2", "label": "ham"},
        {"chunk_id": "c1", "label": "spam"},
    ]
    pool = _ScriptedPool(_ScriptedConn(results))
    paired, *_rest = await matrix_tools.read_paired_rows(pool, TENANT)
    assert [row[0] for row in paired] == ["c1", "c2"]


# ---------------------------------------------------------------------------
# The section builder: honest guards on every state
# ---------------------------------------------------------------------------


def _patch_tables(monkeypatch: pytest.MonkeyPatch, present: bool) -> None:
    async def fake_present(pool: Any) -> bool:
        return present

    monkeypatch.setattr(
        matrix_tools.classification_store, "classification_tables_present", fake_present
    )
    monkeypatch.setattr(matrix_tools.writer, "labeling_tables_present", fake_present)


async def test_missing_tables_answer_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tables(monkeypatch, present=False)
    section = await matrix_tools.eecs_section(_ProbePool(), EECS_ON, TENANT)
    assert section.name == "eecs"
    assert section.status == "unavailable"
    assert "migration" in str(section.detail["reason"])


async def test_no_overlap_is_skipped_with_a_remedy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tables(monkeypatch, present=True)
    results = _paired_results()
    results["FROM chunk_labels"] = [{"chunk_id": "c9", "label": "ham"}]

    async def fake_read(
        pool: Any, tenant_id: str, cap: int = 100000
    ) -> tuple[list[tuple[str, str, str]], int, int, str]:
        return [], 1, 2, "set-1"

    monkeypatch.setattr(matrix_tools, "read_paired_rows", fake_read)
    section = await matrix_tools.eecs_section(_ProbePool(), EECS_ON, TENANT)
    assert section.status == "skipped"
    assert str(section.detail["remedy"])


async def test_success_detail_carries_the_matrix_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tables(monkeypatch, present=True)

    async def fake_read(
        pool: Any, tenant_id: str, cap: int = 100000
    ) -> tuple[list[tuple[str, str, str]], int, int, str]:
        return [("c1", "spam", "spam"), ("c2", "spam", "ham")], 2, 2, "set-1"

    monkeypatch.setattr(matrix_tools, "read_paired_rows", fake_read)
    section = await matrix_tools.eecs_section(_ProbePool(), EECS_ON, TENANT)
    assert section.status == "success"
    assert section.detail["labels"] == ["ham", "spam"]
    # rows = truth, columns = predicted: both truths are "spam"; one
    # predicted spam, one predicted ham.
    assert section.detail["matrix"] == [[0, 0], [1, 1]]
    assert section.detail["paired_count"] == 2
    assert section.detail["set_id"] == "set-1"
    assert {row["label"] for row in section.detail["per_class"]} == {"ham", "spam"}


# ---------------------------------------------------------------------------
# analyze_run wiring: fixed position, no row when off, isolation
# ---------------------------------------------------------------------------


def _patch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_section(pool: Any, config: Any, tenant_id: str) -> report_mod.AnalysisSection:
        return report_mod.AnalysisSection(name="eecs", status="success", detail={"x": 1})

    monkeypatch.setattr(matrix_tools, "eecs_section", fake_section)


async def test_eecs_on_adds_the_section_after_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_success(monkeypatch)
    result = await analysis_tools.analyze_run(_ProbePool(), config=EECS_ON, tenant_id=TENANT)
    assert result["status"] == "success"
    names = [section["name"] for section in result["sections"]]
    assert names == ["dates-coverage", "labeling", "classification", "eecs", "trends"]


async def test_eecs_off_keeps_the_legacy_four_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_success(monkeypatch)

    async def no_tables(pool: Any) -> bool:
        return False

    # The probe turns the classification section unavailable; the point
    # of this test is the section LIST, not its statuses.
    monkeypatch.setattr(
        analysis_tools.classification_store, "classification_tables_present", no_tables
    )
    result = await analysis_tools.analyze_run(
        _ProbePool(), config=CLASSIFICATION_ON, tenant_id=TENANT
    )
    names = [section["name"] for section in result["sections"]]
    assert names == ["dates-coverage", "labeling", "classification", "trends"]


async def test_failing_eecs_section_never_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_tables(monkeypatch, present=True)

    async def boom(pool: Any, tenant_id: str, cap: int = 100000) -> Any:
        raise DatabaseUnavailableError("the database refused the read.")

    monkeypatch.setattr(matrix_tools, "read_paired_rows", boom)
    result = await analysis_tools.analyze_run(_ProbePool(), config=EECS_ON, tenant_id=TENANT)
    # The typed read failure became a failed section; the run itself
    # completed with every section answered (nothing raised).
    eecs = result["sections"][3]
    assert eecs["name"] == "eecs"
    assert eecs["status"] == "failed"
    assert "refused" in str(eecs["detail"]["reason"])
    assert [section["name"] for section in result["sections"]] == [
        "dates-coverage",
        "labeling",
        "classification",
        "eecs",
        "trends",
    ]
