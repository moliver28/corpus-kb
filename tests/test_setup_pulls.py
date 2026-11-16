"""Tests for the pull steps setup runs concurrently (install-ease W1).

The compose pull and the model pulls are the two slow network steps of
setup. These tests drive the error, dry-run, and consent paths of
``setup_pulls`` directly, so a missing asset or a declined prompt
prints its line and never starts a download.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb._setup import setup_assets, setup_stack
from corpus_kb._setup.setup_pulls import (
    collect_model_consents,
    pull_models_consents,
    setup_compose_pull,
    setup_pull_models,
)
from corpus_kb._setup.setup_steps import SETUP_REQUIRED_MODELS


def _write_compose(tmp_path: Path) -> Path:
    """Write a fake compose.yaml and return its path."""
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    return compose


def _record_runs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Patch ``setup_stack._run_cmd`` to record commands and return 0."""
    commands: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> int:
        commands.append(cmd)
        return 0

    monkeypatch.setattr(setup_stack, "_run_cmd", fake_run)
    return commands


def test_compose_pull_missing_file_prints_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing compose.yaml prints the error and returns 1."""
    monkeypatch.setattr(setup_assets, "resolve_setup_asset", lambda _name: None)

    exit_code = setup_compose_pull("docker compose", apply=True)

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "ERROR: compose.yaml not found." in out
    assert "Looked inside the installed package and at the checkout root." in out


def test_compose_pull_dry_run_starts_no_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry run resolves the file, prints nothing extra, and runs nothing."""
    compose = _write_compose(tmp_path)
    monkeypatch.setattr(setup_assets, "resolve_setup_asset", lambda _name: compose)
    commands = _record_runs(monkeypatch)

    exit_code = setup_compose_pull("docker compose", apply=False)

    assert exit_code == 0
    assert commands == []


def test_compose_pull_asks_when_consent_was_not_collected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no collected consent, the pull asks and runs on agreement."""
    compose = _write_compose(tmp_path)
    monkeypatch.setattr(setup_assets, "resolve_setup_asset", lambda _name: compose)
    questions: list[str] = []

    def fake_confirm(question: str) -> bool:
        questions.append(question)
        return True

    monkeypatch.setattr(setup_stack, "_confirm_fn", lambda: fake_confirm)
    commands = _record_runs(monkeypatch)

    exit_code = setup_compose_pull("docker compose", apply=True, agreed=None)

    assert exit_code == 0
    assert questions == ["Pull the Docker images for the compose stack."]
    assert commands == [["docker", "compose", "-f", str(compose), "pull"]]


def test_compose_pull_declined_starts_no_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A declined consent prints the skip line and runs nothing."""
    compose = _write_compose(tmp_path)
    monkeypatch.setattr(setup_assets, "resolve_setup_asset", lambda _name: compose)
    commands = _record_runs(monkeypatch)

    exit_code = setup_compose_pull("docker compose", apply=True, agreed=False)

    assert exit_code == 0
    assert commands == []
    assert "  Skipped." in capsys.readouterr().out


def test_pull_models_consents_skips_declined_models(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A declined model prints its skip line and starts no download."""
    commands = _record_runs(monkeypatch)
    consents = [(model, False) for model in SETUP_REQUIRED_MODELS]

    exit_code = pull_models_consents(consents)

    assert exit_code == 0
    assert commands == []
    out = capsys.readouterr().out
    for model in SETUP_REQUIRED_MODELS:
        assert f"  Skipped {model}." in out


def test_pull_models_consents_pulls_agreed_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each agreed model pulls in order; one failure makes the exit nonzero."""
    commands = _record_runs(monkeypatch)
    consents = [(SETUP_REQUIRED_MODELS[0], True), (SETUP_REQUIRED_MODELS[1], False)]

    assert pull_models_consents(consents) == 0
    assert commands == [["ollama", "pull", SETUP_REQUIRED_MODELS[0]]]

    def failing_run(cmd: list[str], **_kwargs: object) -> int:
        commands.append(cmd)
        return 1

    monkeypatch.setattr(setup_stack, "_run_cmd", failing_run)
    assert pull_models_consents([(SETUP_REQUIRED_MODELS[0], True)]) == 1


def test_collect_model_consents_asks_each_model_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The collector asks one question per required model, in declared order."""
    questions: list[str] = []

    def fake_confirm(question: str) -> bool:
        questions.append(question)
        return len(questions) == 1  # Agree to the first model, decline the rest.

    monkeypatch.setattr(setup_stack, "_confirm_fn", lambda: fake_confirm)

    consents = collect_model_consents()

    assert questions == [f"Pull Ollama model '{model}'." for model in SETUP_REQUIRED_MODELS]
    assert consents == [(model, i == 0) for i, model in enumerate(SETUP_REQUIRED_MODELS)]


def test_setup_pull_models_dry_run_prints_plan(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dry run prints each pull command and starts no download."""
    commands = _record_runs(monkeypatch)

    exit_code = setup_pull_models(apply=False)

    assert exit_code == 0
    assert commands == []
    out = capsys.readouterr().out
    for model in SETUP_REQUIRED_MODELS:
        assert f"  (dry run) ollama pull {model}" in out


def test_setup_pull_models_asks_when_consents_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no collected consents, each model asks and pulls on agreement."""
    questions: list[str] = []

    def fake_confirm(question: str) -> bool:
        questions.append(question)
        return True

    monkeypatch.setattr(setup_stack, "_confirm_fn", lambda: fake_confirm)
    commands = _record_runs(monkeypatch)

    exit_code = setup_pull_models(apply=True, consents=None)

    assert exit_code == 0
    assert questions == [f"Pull Ollama model '{model}'." for model in SETUP_REQUIRED_MODELS]
    assert commands == [["ollama", "pull", model] for model in SETUP_REQUIRED_MODELS]
