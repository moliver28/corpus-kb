"""The composed analyze_run (WP-5): sections, isolation, artifact.

Everything runs at the handler-module seam: ``analysis_tools`` calls
its feature handlers late (``from . import labeling_tools`` and
friends, then ``labeling_tools.labeling_build(...)``), so each test
monkeypatches the module attribute and no database is touched. The
pool is a probe object the patched handlers never use.

Covered here: the exact dormant stub envelope, the fixed section
order, per-section failure isolation (one handler raising a typed
error keeps the run honest), the classification skip/probe contract,
the dates-refresh-before-trends order, and the deterministic output
artifact (JSON plus rendered markdown, byte-identical across runs).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from corpus_kb.errors import DependencyMissingError, ValidationError
from corpus_kb.tools import analysis_tools

TENANT = "00000000-0000-0000-0000-000000000001"

OFF = {}
LABELING_ON = {"analysis": {"labeling": {"enabled": True, "lfs_file": "lfs.json"}}}
CLASSIFICATION_ON = {"analysis": {"classification": {"enabled": True}}}
OVER_TIME_ON = {"analysis": {"topics": {"over_time": {"enabled": True}}}}
LABELING_PLUS_TRENDS = {
    "analysis": {
        "labeling": {"enabled": True, "lfs_file": "lfs.json"},
        "topics": {"over_time": {"enabled": True}},
    }
}
ALL_ON = {
    "analysis": {
        "labeling": {"enabled": True, "lfs_file": "lfs.json"},
        "classification": {"enabled": True},
        "topics": {"over_time": {"enabled": True}},
    }
}

STUB_DISABLED_ENVELOPE = {
    "status": "disabled",
    "reason": (
        "the analysis features are off in the loaded config "
        "(analysis.labeling.enabled, analysis.classification.enabled, "
        "analysis.topics.over_time.enabled are all false)."
    ),
    "remedy": "Set the analysis.*.enabled flags you need to true, then retry.",
    "sections": [],
}


class _ProbePool:
    """The patched handlers never touch it; any use fails loudly."""

    def __getattr__(self, name: str) -> Callable[..., object]:
        def _nope(*args: object, **kwargs: object) -> object:
            raise AssertionError(f"the pool method {name} must not run in this test")

        return _nope


def _returns(result: dict[str, object]) -> Callable[..., Awaitable[dict[str, object]]]:
    async def fake(*args: object, **kwargs: object) -> dict[str, object]:
        return result

    return fake


def _returns_bool(value: bool) -> Callable[..., Awaitable[bool]]:
    async def fake(*args: object, **kwargs: object) -> bool:
        return value

    return fake


def _raises(exc: BaseException) -> Callable[..., Awaitable[dict[str, object]]]:
    async def fake(*args: object, **kwargs: object) -> dict[str, object]:
        raise exc

    return fake


def _backfill() -> dict[str, object]:
    return {
        "status": "success",
        "documents_scanned": 4,
        "documents_dated": 3,
        "params": {"rule_version": "2026-10-19"},
    }


def _labels() -> dict[str, object]:
    return {
        "status": "success",
        "label_count": 2,
        "labels": [{"chunk_id": "c1", "label": "relevant", "probability": 0.9}],
        "params": {"lfs_file": "lfs.json", "min_signals": 3},
    }


def _prediction_row() -> dict[str, object]:
    return {
        "chunk_id": "00000000-0000-0000-0000-0000000000aa",
        "set_id": "00000000-0000-0000-0000-0000000000bb",
        "label": "alpha",
        "probability": 0.9,
        "calibrated_probability": 0.8,
        "tier": "head",
        "review": False,
    }


def _trends() -> dict[str, object]:
    return {
        "status": "success",
        "topic_count": 1,
        "trends": [{"topic": 0, "series": [1, 2]}],
        "dated_count": 3,
        "undated_count": 1,
        "bins": 10,
        "params": {"bins": 10},
    }


def _patch_all_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis_tools, "analysis_backfill_dates", _returns(_backfill()))
    monkeypatch.setattr(analysis_tools.labeling_tools, "labeling_build", _returns(_labels()))
    monkeypatch.setattr(
        analysis_tools.classification_store, "classification_tables_present", _returns_bool(True)
    )
    monkeypatch.setattr(
        analysis_tools.classification_store,
        "read_predictions",
        _returns([_prediction_row()]),
    )

    async def _fake_count(*args: object, **kwargs: object) -> tuple[int, int]:
        return (5, 1)

    monkeypatch.setattr(analysis_tools.classification_store, "count_predictions", _fake_count)
    monkeypatch.setattr(
        analysis_tools.classification_tools,
        "classification_list",
        _returns({"status": "success", "prediction_count": 5, "predictions": [_prediction_row()]}),
    )
    monkeypatch.setattr(analysis_tools.topics_tools, "topics_trends", _returns(_trends()))


# ---------------------------------------------------------------------------
# The dormant default
# ---------------------------------------------------------------------------


async def test_all_off_returns_the_exact_stub_disabled_envelope() -> None:
    result = await analysis_tools.analyze_run(_ProbePool(), config=OFF, tenant_id=TENANT)
    assert result == STUB_DISABLED_ENVELOPE


async def test_all_off_writes_nothing_even_with_an_output_path(
    tmp_path: Path,
) -> None:
    result = await analysis_tools.analyze_run(
        _ProbePool(), config=OFF, tenant_id=TENANT, output=str(tmp_path / "report.json")
    )
    assert "report_path" not in result
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Sections: fixed order, per-feature gates, failure isolation
# ---------------------------------------------------------------------------


async def test_every_flag_on_gives_four_success_sections_in_fixed_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_all_success(monkeypatch)
    result = await analysis_tools.analyze_run(_ProbePool(), config=ALL_ON, tenant_id=TENANT)
    assert result["status"] == "success"
    sections = result["sections"]
    assert isinstance(sections, list)
    assert [section["name"] for section in sections] == [
        "dates-coverage",
        "labeling",
        "classification",
        "trends",
    ]
    assert [section["status"] for section in sections] == ["success"] * 4


async def test_labeling_without_lfs_file_is_disabled_naming_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        analysis_tools.labeling_tools,
        "labeling_build",
        _raises(AssertionError("the labeling build must not run")),
    )
    result = await analysis_tools.analyze_run(
        _ProbePool(), config={"analysis": {"labeling": {"enabled": True}}}, tenant_id=TENANT
    )
    assert isinstance(result["sections"], list)
    labeling = result["sections"][1]
    assert labeling["name"] == "labeling"
    assert labeling["status"] == "disabled"
    assert "lfs_file" in labeling["detail"]["reason"]
    # The corner: flags on, no section succeeded -> disabled with a reason.
    assert result["status"] == "disabled"
    assert result["reason"] == "the analysis flags are on but no section produced data."


async def test_failed_labeling_section_does_not_fail_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = DependencyMissingError("The labeling extra is missing.")
    monkeypatch.setattr(analysis_tools.labeling_tools, "labeling_build", _raises(failure))
    monkeypatch.setattr(analysis_tools, "analysis_backfill_dates", _returns(_backfill()))
    monkeypatch.setattr(analysis_tools.topics_tools, "topics_trends", _returns(_trends()))
    result = await analysis_tools.analyze_run(
        _ProbePool(), config=LABELING_PLUS_TRENDS, tenant_id=TENANT
    )
    assert result["status"] == "success"
    labeling = result["sections"][1]
    assert labeling["status"] == "failed"
    assert labeling["detail"]["reason"] == "The labeling extra is missing."
    assert labeling["detail"]["remedy"] == DependencyMissingError.remediation


async def test_value_error_failure_carries_no_remedy_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        analysis_tools.labeling_tools,
        "labeling_build",
        _raises(ValueError("LF file lfs.json is not valid JSON: x (line 1).")),
    )
    monkeypatch.setattr(analysis_tools, "analysis_backfill_dates", _returns(_backfill()))
    monkeypatch.setattr(analysis_tools.topics_tools, "topics_trends", _returns(_trends()))
    result = await analysis_tools.analyze_run(
        _ProbePool(), config=LABELING_PLUS_TRENDS, tenant_id=TENANT
    )
    assert result["status"] == "success"
    labeling = result["sections"][1]
    assert labeling["status"] == "failed"
    assert "not valid JSON" in labeling["detail"]["reason"]
    assert "remedy" not in labeling["detail"]


async def test_validation_error_failure_keeps_the_same_row_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The labeling loader's typed rejection keeps the legacy envelope.

    Wave 1 (issue 229) retypes the loader's raises. The failed section
    must keep the reason-only row: no generic remedy row appears.
    """
    monkeypatch.setattr(
        analysis_tools.labeling_tools,
        "labeling_build",
        _raises(ValidationError("LF file lfs.json is not valid JSON: x (line 1).")),
    )
    monkeypatch.setattr(analysis_tools, "analysis_backfill_dates", _returns(_backfill()))
    monkeypatch.setattr(analysis_tools.topics_tools, "topics_trends", _returns(_trends()))
    result = await analysis_tools.analyze_run(
        _ProbePool(), config=LABELING_PLUS_TRENDS, tenant_id=TENANT
    )
    assert result["status"] == "success"
    labeling = result["sections"][1]
    assert labeling["status"] == "failed"
    assert "not valid JSON" in labeling["detail"]["reason"]
    assert "remedy" not in labeling["detail"]


# ---------------------------------------------------------------------------
# Classification: the seed-set probe
# ---------------------------------------------------------------------------


async def test_classification_without_a_seed_set_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        analysis_tools.classification_store, "classification_tables_present", _returns_bool(True)
    )
    monkeypatch.setattr(analysis_tools.classification_store, "read_predictions", _returns([]))
    monkeypatch.setattr(
        analysis_tools.classification_tools,
        "classification_build",
        _raises(AssertionError("the classification build must not run")),
    )
    result = await analysis_tools.analyze_run(
        _ProbePool(), config=CLASSIFICATION_ON, tenant_id=TENANT
    )
    classification = result["sections"][2]
    assert classification["name"] == "classification"
    assert classification["status"] == "skipped"
    reason = classification["detail"]["reason"]
    assert reason == "no seed label set; run classification-build with a seed file first"


async def test_classification_with_a_stored_set_reports_it_without_a_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        analysis_tools.classification_store, "classification_tables_present", _returns_bool(True)
    )
    monkeypatch.setattr(
        analysis_tools.classification_store,
        "read_predictions",
        _returns([_prediction_row()]),
    )

    async def _fake_count(*args: object, **kwargs: object) -> tuple[int, int]:
        return (1200, 37)

    monkeypatch.setattr(analysis_tools.classification_store, "count_predictions", _fake_count)
    monkeypatch.setattr(
        analysis_tools.classification_tools,
        "classification_list",
        _returns({"status": "success", "prediction_count": 50, "predictions": [_prediction_row()]}),
    )
    monkeypatch.setattr(
        analysis_tools.classification_tools,
        "classification_build",
        _raises(AssertionError("the classification build must not run in the composed report")),
    )
    result = await analysis_tools.analyze_run(
        _ProbePool(), config=CLASSIFICATION_ON, tenant_id=TENANT
    )
    classification = result["sections"][2]
    assert classification["status"] == "success"
    detail = classification["detail"]
    assert detail["prediction_count"] == 1200
    assert detail["review_count"] == 37
    assert detail["sample"] == [_prediction_row()]


# ---------------------------------------------------------------------------
# Trends: refresh dates first (the rebuild contract)
# ---------------------------------------------------------------------------


async def test_trends_refreshes_dates_before_the_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_backfill(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append("backfill")
        return _backfill()

    async def fake_trends(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append("trends")
        return _trends()

    monkeypatch.setattr(analysis_tools, "analysis_backfill_dates", fake_backfill)
    monkeypatch.setattr(analysis_tools.topics_tools, "topics_trends", fake_trends)
    result = await analysis_tools.analyze_run(_ProbePool(), config=OVER_TIME_ON, tenant_id=TENANT)
    # One backfill for the dates-coverage section, one as the trends refresh;
    # the refresh lands strictly before the trends fit (idempotent, cheap).
    assert calls == ["backfill", "backfill", "trends"]
    trends = result["sections"][3]
    assert trends["status"] == "success"
    assert trends["detail"]["date_backfill"] == {"status": "success", "documents_dated": 3}


# ---------------------------------------------------------------------------
# The artifact: written, deterministic, absent without an output path
# ---------------------------------------------------------------------------


async def test_output_writes_deterministic_json_and_markdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_all_success(monkeypatch)
    first = tmp_path / "one" / "report.json"
    second = tmp_path / "two" / "report.json"
    run_one = await analysis_tools.analyze_run(
        _ProbePool(), config=ALL_ON, tenant_id=TENANT, output=str(first)
    )
    run_two = await analysis_tools.analyze_run(
        _ProbePool(), config=ALL_ON, tenant_id=TENANT, output=str(second)
    )
    assert run_two["report_path"] == str(second)
    assert run_one["report_path"] == str(first)
    assert first.read_bytes() == second.read_bytes()
    markdown_one = first.with_suffix(".md")
    assert markdown_one.is_file()
    assert markdown_one.read_bytes() == second.with_suffix(".md").read_bytes()
    parsed = json.loads(first.read_text(encoding="utf-8"))
    assert parsed["status"] == "success"
    assert [section["name"] for section in parsed["sections"]] == [
        "dates-coverage",
        "labeling",
        "classification",
        "trends",
    ]
    rendered = markdown_one.read_text(encoding="utf-8")
    assert "# Corpus analysis report" in rendered
    assert "## trends" in rendered


async def test_no_artifact_when_output_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_all_success(monkeypatch)
    result = await analysis_tools.analyze_run(_ProbePool(), config=ALL_ON, tenant_id=TENANT)
    assert result["status"] == "success"
    assert "report_path" not in result
    assert list(tmp_path.iterdir()) == []
