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

    MARKER = "LEAK-MARKER-0123456789abcdef0123456789abcdef01"

    async def handler(**_kwargs: object) -> dict[str, object]:
        return {"status": "success", "chunks": [MARKER]}

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
    assert MARKER not in lines[0]


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

