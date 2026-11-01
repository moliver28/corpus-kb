"""The labeling-triage / labeling-gold-check CLI arms (the cli_graph pattern).

The pool and config are faked at the CLI seams and the shared tool
function is a recorder, so each test pins ONE thing: the flags the
command forwards, the envelope it prints, and the honest exit code.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app

runner = CliRunner()

_TENANT = "00000000-0000-0000-0000-000000000001"

_TRIAGE = {
    "status": "success",
    "n_flagged": 1,
    "queue": [
        {
            "chunk_id": "m00000000000",
            "assigned_label": "b",
            "oos_probability": 0.05,
            "runner_up_label": "a",
            "runner_up_probability": 0.9,
        }
    ],
}
_GOLD = {
    "status": "success",
    "class_count": 1,
    "validations": [{"class_name": "reports", "status": "corrected", "raw_accuracy": 0.8}],
}


class _RecordingTool:
    """The shared tool function stand-in: records, answers one envelope."""

    def __init__(self, envelope: dict[str, object]) -> None:
        self.envelope = envelope
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        self.calls.append({"args": args, **kwargs})
        return self.envelope


class _FakePool:
    closed = False

    async def __call__(self, _cfg: object) -> _FakePool:
        return self

    async def close(self) -> None:
        self.closed = True


def _patch_world(
    monkeypatch: pytest.MonkeyPatch, module: str, fn_name: str, fake: _RecordingTool
) -> _FakePool:
    """Fake the config, the pool, and the shared tool function."""

    pool = _FakePool()

    async def _open(_cfg: object) -> _FakePool:
        return pool

    def _cfg() -> dict[str, object]:
        return {"analysis": {"labeling": {}}}

    monkeypatch.setattr("corpus_kb.cli.cli_labeling._open_pool", _open)
    monkeypatch.setattr("corpus_kb.cli.cli_labeling._load_config", _cfg)
    monkeypatch.setattr(f"{module}.{fn_name}", fake)
    return pool


def test_labeling_triage_json_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_TRIAGE)
    pool = _patch_world(
        monkeypatch, "corpus_kb.tools.labeling_triage_tools", "labeling_triage", fake
    )
    result = runner.invoke(app, ["labeling-triage", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _TRIAGE
    call = fake.calls[0]
    assert call["args"][0] is pool
    assert call["cv_folds"] == 5
    assert call["flag_threshold"] == 0.2
    assert call["limit"] == 50
    assert pool.closed is True


def test_labeling_triage_forwards_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(_TRIAGE)
    _patch_world(monkeypatch, "corpus_kb.tools.labeling_triage_tools", "labeling_triage", fake)
    result = runner.invoke(
        app,
        ["labeling-triage", "--cv-folds", "3", "--flag-threshold", "0.5", "--limit", "7"],
    )
    assert result.exit_code == 0, result.output
    assert fake.calls[0]["cv_folds"] == 3
    assert fake.calls[0]["flag_threshold"] == 0.5
    assert fake.calls[0]["limit"] == 7


def test_labeling_gold_check_json_envelope(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    fake = _RecordingTool(_GOLD)
    pool = _patch_world(
        monkeypatch, "corpus_kb.tools.labeling_gold_tools", "labeling_gold_check", fake
    )
    gold_file = tmp_path / "gold.json"
    gold_file.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["labeling-gold-check", "--gold-file", str(gold_file), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _GOLD
    call = fake.calls[0]
    assert call["gold_file"] == str(gold_file)
    assert call["confidence_level"] == 0.95
    assert pool.closed is True


def test_labeling_gold_check_requires_the_gold_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _RecordingTool(_GOLD)
    _patch_world(monkeypatch, "corpus_kb.tools.labeling_gold_tools", "labeling_gold_check", fake)
    result = runner.invoke(app, ["labeling-gold-check"])
    assert result.exit_code != 0
    assert fake.calls == []


@pytest.mark.parametrize(
    ("envelope", "expected"),
    [
        ({"status": "insufficient_data", "reason": "thin", "remedy": "retry"}, 1),
        ({"status": "unavailable", "reason": "no tables", "remedy": "setup"}, 3),
    ],
)
def test_honest_exit_codes(
    monkeypatch: pytest.MonkeyPatch, envelope: dict[str, object], expected: int
) -> None:
    fake = _RecordingTool(envelope)
    _patch_world(monkeypatch, "corpus_kb.tools.labeling_triage_tools", "labeling_triage", fake)
    result = runner.invoke(app, ["labeling-triage", "--json"])
    assert result.exit_code == expected, result.output
    assert json.loads(result.stdout) == envelope


def test_human_output_names_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _RecordingTool(
        {"status": "insufficient_data", "reason": "too few units.", "remedy": "Label more."}
    )
    _patch_world(monkeypatch, "corpus_kb.tools.labeling_triage_tools", "labeling_triage", fake)
    result = runner.invoke(app, ["labeling-triage"])
    assert result.exit_code == 1, result.output
    assert "insufficient_data" in result.output
    assert "too few units." in result.output
    assert "remedy: Label more." in result.output


def test_labeling_gold_check_human_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    fake = _RecordingTool(_GOLD)
    _patch_world(monkeypatch, "corpus_kb.tools.labeling_gold_tools", "labeling_gold_check", fake)
    gold_file = tmp_path / "gold.json"
    gold_file.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["labeling-gold-check", "--gold-file", str(gold_file)])
    assert result.exit_code == 0, result.output
    assert "labeling-gold-check: 1 classes validated" in result.output
