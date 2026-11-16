"""Tests for the opt-in telemetry seam (issue #442).

The feature is OFF by default: every default-shape config, every CI run,
and every disabled emit writes nothing. The sink whitelists six event
keys and never raises. The report script is byte-deterministic for the
same log directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_kb.config import get_default_config
from corpus_kb.telemetry.settings import (
    load_telemetry_settings,
    telemetry_settings_error,
)


def test_telemetry_is_off_by_default() -> None:
    assert get_default_config()["telemetry"]["enabled"] is False
    assert load_telemetry_settings({}).enabled is False


def test_settings_read_partial_block(tmp_path: Path) -> None:
    settings = load_telemetry_settings(
        {"installer": {"data_dir": str(tmp_path)}, "telemetry": {"enabled": True}}
    )
    assert settings.enabled is True
    assert settings.log_dir == tmp_path / "telemetry_logs"  # F18: resolved, absolute


def test_settings_reject_non_bool_flag() -> None:
    error = telemetry_settings_error({"telemetry": {"enabled": "yes"}})
    assert error is not None and "must be true or false" in error


def test_relative_log_dir_never_resolves_against_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = load_telemetry_settings(
        {
            "installer": {"data_dir": str(tmp_path / "data")},
            "telemetry": {"log_dir": "logs"},
        }
    )
    assert settings.log_dir == tmp_path / "data" / "logs"  # F18: installer data dir


def test_absolute_log_dir_passes_through(tmp_path: Path) -> None:
    settings = load_telemetry_settings({"telemetry": {"log_dir": str(tmp_path / "direct")}})
    assert settings.log_dir == tmp_path / "direct"


def test_default_config_carries_the_block() -> None:
    assert get_default_config()["telemetry"]["log_dir"] == "telemetry_logs"  # portable (F18)


# ---------------------------------------------------------------------------
# Task 2: the sink and the session tracker
# ---------------------------------------------------------------------------


def test_write_event_appends_one_whitelisted_line(tmp_path: Path) -> None:
    from corpus_kb.telemetry.sink import Event, write_event

    log = tmp_path / "events.jsonl"
    write_event(
        log,
        Event(
            method_id="02",
            success=True,
            outcome="ok",
            duration=3,
            first_attempt=True,
            session_ordinal=1,
        ),
    )
    write_event(
        log,
        Event(
            method_id="02",
            success=True,
            outcome="ok",
            duration=5,
            first_attempt=False,
            session_ordinal=2,
        ),
    )
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert set(first) == {
        "method_id",
        "success",
        "outcome",
        "duration",
        "first_attempt",
        "session_ordinal",
        "schema_version",
    }
    assert first["schema_version"] == "1"


def test_write_event_creates_missing_dirs(tmp_path: Path) -> None:
    from corpus_kb.telemetry.sink import Event, write_event

    log = tmp_path / "deep" / "nested" / "events.jsonl"
    write_event(
        log,
        Event(
            method_id="30",
            success=True,
            outcome="ok",
            duration=1,
            first_attempt=True,
            session_ordinal=1,
        ),
    )
    assert log.exists()


def test_tracker_ordinals_are_per_method() -> None:
    from corpus_kb.telemetry.session import SessionTracker

    tracker = SessionTracker()
    assert tracker.current_ordinal("02") == 0
    assert tracker.first_attempt("02")
    assert tracker.bump("02") == 1  # bump() is the only mutator (F4)
    assert tracker.current_ordinal("02") == 1
    assert not tracker.first_attempt("02")  # reads never mutate; the bump above did
    assert tracker.bump("02") == 2
    assert tracker.first_attempt("04")


def test_sink_never_raises_and_flags_suppression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from corpus_kb.telemetry import sink
    from corpus_kb.telemetry.sink import Event, write_event

    monkeypatch.setattr(sink, "_suppressed", False)
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("file in the way", encoding="utf-8")
    log = blocked / "events.jsonl"  # parent is a FILE: every write fails
    event = Event(
        method_id="12",
        success=False,
        outcome="database_unavailable",
        duration=2,
        first_attempt=True,
        session_ordinal=1,
    )
    for _ in range(100):
        write_event(log, event)  # must never raise (F5)
    assert sink._suppressed is True
    assert not log.exists()


def test_emit_skips_none_method_id(tmp_path: Path) -> None:
    from corpus_kb.telemetry import emit

    async def handler(**_kwargs: object) -> dict[str, object]:
        return {"status": "success"}

    config = {"telemetry": {"enabled": True, "log_dir": str(tmp_path)}}
    result = _run(emit(handler, method_id=None, config=config))
    assert result == {"status": "success"}
    assert not (tmp_path / "events.jsonl").exists()  # F6: no Event, no line


def test_emit_records_success_and_keeps_envelope(tmp_path: Path) -> None:
    from corpus_kb.telemetry import emit

    marker = "LEAK-MARKER-0123456789abcdef0123456789abcdef01"

    async def handler(**_kwargs: object) -> dict[str, object]:
        return {"status": "success", "chunks": [marker]}

    config = {"telemetry": {"enabled": True, "log_dir": str(tmp_path)}}
    result = _run(emit(handler, method_id="12", config=config))
    assert result["status"] == "success"
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["method_id"] == "12"
    assert row["success"] is True
    assert row["outcome"] == "ok"
    assert isinstance(row["duration"], int)
    assert isinstance(row["first_attempt"], bool)
    assert row["session_ordinal"] >= 1
    # Review Focus 3: no content ever reaches the log.
    assert marker not in lines[0]


def test_emit_records_typed_error_and_reraises(tmp_path: Path) -> None:
    from corpus_kb.errors import ValidationError
    from corpus_kb.telemetry import emit

    async def handler(**_kwargs: object) -> dict[str, object]:
        raise ValidationError("bad input")

    config = {"telemetry": {"enabled": True, "log_dir": str(tmp_path)}}
    with pytest.raises(ValidationError):
        _run(emit(handler, method_id="12", config=config))
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    row = json.loads(lines[0])
    assert row["success"] is False
    assert row["outcome"] == "validation_error"  # taxonomy class, never message text


def test_emit_records_plain_error_and_disabled_status(tmp_path: Path) -> None:
    from corpus_kb.telemetry import emit

    async def failing(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("boom")

    async def disabled() -> dict[str, object]:
        return {"status": "disabled"}

    config = {"telemetry": {"enabled": True, "log_dir": str(tmp_path)}}
    with pytest.raises(RuntimeError):
        _run(emit(failing, method_id="12", config=config))
    _run(emit(disabled, method_id="12", config=config))
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    outcomes = [json.loads(line)["outcome"] for line in lines]
    successes = [json.loads(line)["success"] for line in lines]
    assert outcomes == ["error", "ok"]  # RuntimeError has no taxonomy class
    assert successes == [False, True]  # "disabled" is an honest-ok status


def _run(awaitable: object) -> object:
    import asyncio

    return asyncio.run(awaitable)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Task 3: the attribution table and the four adapter call sites
# ---------------------------------------------------------------------------


def test_attribution_map_names_all_18_ops() -> None:
    from corpus_kb.telemetry.attribution import METHOD_FOR_OP

    assert set(METHOD_FOR_OP) == {
        "analyze_run",
        "labeling_build",
        "topics_build",
        "keyphrases_extract",
        "clusters_inspect",
        "centroids_classify",
        "neighbors_classify",
        "lexical_classify",
        "classification_build",
        "nli_classify",
        "active_learn_next",
        "search",
        "ask",
        "ingest_text",
        "sentiment_screen",
        "contradictions_screen",
        "duplicates_screen",
        "outliers_rank",
    }
    assert METHOD_FOR_OP["clusters_inspect"] == "12"
    assert METHOD_FOR_OP["outliers_rank"] == "30"


def test_shared_ops_are_declared_null() -> None:
    from corpus_kb.telemetry.attribution import METHOD_FOR_OP

    for op in ("analyze_run", "labeling_build", "topics_build", "classification_build"):
        assert METHOD_FOR_OP[op] is None


def test_method_id_for_op_discriminates_keyphrases_variant() -> None:
    from corpus_kb.telemetry.attribution import method_id_for_op

    assert method_id_for_op("keyphrases_extract", {"variant": "tfidf"}) == "05"
    assert method_id_for_op("keyphrases_extract", {"variant": "embedding"}) == "06"
    assert method_id_for_op("keyphrases_extract", {"variant": "llm"}) == "07"
    assert method_id_for_op("keyphrases_extract", {}) == "05"  # spec default (F13)
    assert method_id_for_op("keyphrases_extract", {"variant": "nope"}) is None
    assert method_id_for_op("clusters_inspect", {}) == "12"
    assert method_id_for_op("sentiment_screen", {"chunk_ids": [1]}) is None
    assert method_id_for_op("ingest_directory", {}) is None  # not a register op


def _flow_env(tmp_path: Path, enabled: bool, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch the cli_ingest seams so flow() runs against fakes."""
    from corpus_kb.cli import cli_ingest

    cfg: dict[str, object] = {
        "telemetry": {"enabled": enabled, "log_dir": str(tmp_path)},
        "database": {"backend": "duckdb"},
    }
    monkeypatch.setattr(cli_ingest, "_load_config", lambda: cfg)
    monkeypatch.setattr(cli_ingest, "_open_pool", _fake_open_pool)
    return tmp_path


async def _fake_open_pool(_cfg: dict[str, object]) -> object:
    class _Pool:
        async def close(self) -> None:
            return None

    return _Pool()


def test_flow_records_one_line_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio as _asyncio

    from corpus_kb.cli._plumbing import flow
    from corpus_kb.tools import cluster_tools

    async def fake_clusters_inspect(
        _pool: object, _cfg: object, **_kwargs: object
    ) -> dict[str, object]:
        return {"status": "success"}

    monkeypatch.setattr(cluster_tools, "clusters_inspect", fake_clusters_inspect)
    _flow_env(tmp_path, True, monkeypatch)
    exit_code = _asyncio.run(
        flow(
            "cluster_tools",
            "clusters_inspect",
            {},
            False,
            lambda _result, _json: None,
        )
    )
    assert exit_code == 0
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["method_id"] == "12"


def test_flow_records_nothing_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio as _asyncio

    from corpus_kb.cli._plumbing import flow
    from corpus_kb.tools import cluster_tools

    async def fake_clusters_inspect(
        _pool: object, _cfg: object, **_kwargs: object
    ) -> dict[str, object]:
        return {"status": "success"}

    monkeypatch.setattr(cluster_tools, "clusters_inspect", fake_clusters_inspect)
    _flow_env(tmp_path, False, monkeypatch)
    exit_code = _asyncio.run(
        flow(
            "cluster_tools",
            "clusters_inspect",
            {},
            False,
            lambda _result, _json: None,
        )
    )
    assert exit_code == 0
    assert not (tmp_path / "events.jsonl").exists()


def test_flow_keyphrases_discriminates_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio as _asyncio

    from corpus_kb.cli._plumbing import flow
    from corpus_kb.tools import keyphrase_tools

    async def fake_keyphrases(_pool: object, _cfg: object, **_kwargs: object) -> dict[str, object]:
        return {"status": "success"}

    monkeypatch.setattr(keyphrase_tools, "keyphrases_extract", fake_keyphrases)
    _flow_env(tmp_path, True, monkeypatch)
    _asyncio.run(
        flow(
            "keyphrase_tools",
            "keyphrases_extract",
            {"variant": "llm"},
            False,
            lambda _result, _json: None,
        )
    )
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[0])["method_id"] == "07"


# ---------------------------------------------------------------------------
# Task 5: the consistency report script
# ---------------------------------------------------------------------------


def test_report_is_deterministic_on_the_fixture() -> None:
    from scripts.telemetry_report import consistency_report

    one = consistency_report("tests/fixtures/telemetry")
    two = consistency_report("tests/fixtures/telemetry")
    assert one == two


def test_report_mentions_corrupted_lines() -> None:
    from scripts.telemetry_report import consistency_report

    text = consistency_report("tests/fixtures/telemetry")
    assert "skipped" in text


def test_report_never_writes(tmp_path: Path) -> None:
    from scripts.telemetry_report import consistency_report

    before = sorted(p.name for p in tmp_path.iterdir())
    consistency_report("tests/fixtures/telemetry")
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_report_header_names_gaps_and_order_source() -> None:
    from scripts.telemetry_report import consistency_report

    text = consistency_report("tests/fixtures/telemetry")
    assert "derive_edges" in text  # F3: the ladder order source is named
    assert "ingest_text" in text  # the declared socket no-event gap
    assert "null" in text  # the shared-op attribution caveat


def test_report_flags_concept_divergence() -> None:
    from scripts.telemetry_report import consistency_report

    text = consistency_report("tests/fixtures/telemetry")
    # concepts_fixture: 05 succeeds on its first attempt, 15 fails its
    # first attempt; both teach term_weighting -> gap 1.0 > 0.5.
    assert "term_weighting" in text


def test_ladder_order_is_total_and_deterministic() -> None:
    from scripts.telemetry_report import _ladder_order

    register = _register_for_tests()
    order = _ladder_order(register.methods)
    assert sorted(order) == sorted(record.id for record in register.methods)
    assert order == _ladder_order(register.methods)


def _register_for_tests() -> object:
    from corpus_kb.analysis.method_register_loader import load_method_register

    return load_method_register()


# ---------------------------------------------------------------------------
# Task 6: the zero-write proof and the off-by-default CI proof
# ---------------------------------------------------------------------------


async def test_no_writes_when_disabled(tmp_path: Path) -> None:
    from corpus_kb.telemetry import emit

    async def handler(**_kwargs: object) -> dict[str, object]:
        return {"status": "success"}

    config = {"telemetry": {"enabled": False, "log_dir": str(tmp_path)}}
    for _ in range(5):
        result = await emit(handler, method_id="12", config=config)
        assert result["status"] == "success"
    assert not (tmp_path / "events.jsonl").exists()


def test_off_by_default_ci_proof() -> None:
    """The shipped shape must never enable telemetry (Review Focus 2)."""
    from corpus_kb.telemetry.sink import EVENT_SCHEMA_VERSION

    assert get_default_config()["telemetry"]["enabled"] is False
    assert load_telemetry_settings({}).enabled is False
    assert load_telemetry_settings({}).log_dir.name == "telemetry_logs"
    assert EVENT_SCHEMA_VERSION == "1"
