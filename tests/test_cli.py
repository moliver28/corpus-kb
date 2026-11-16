"""Tests for the ``corpus-kb`` Typer CLI."""

from __future__ import annotations

import re
from typing import Any

from typer.testing import CliRunner

from corpus_kb.cli import app

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI color escape sequences from ``text``.

    Typer/Rich inject these codes into help output in TTY-like environments,
    which breaks substring assertions on flag tokens like ``--build-local``
    that span a styled segment.
    """
    return _ANSI_RE.sub("", text)


def test_cli_help_returns_zero() -> None:
    """``corpus-kb --help`` exits cleanly."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Corpus-KB command-line interface" in result.output


def test_setup_command_registered() -> None:
    """The setup command appears in --help."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "setup" in result.output


def test_doctor_command_registered() -> None:
    """The doctor command appears in --help."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output


def test_start_command_registered() -> None:
    """The start command appears in --help."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "start" in result.output


def test_setup_help_includes_build_local() -> None:
    """The setup command exposes a --build-local flag."""
    result = runner.invoke(app, ["setup", "--help"])
    assert result.exit_code == 0
    assert "--build-local" in _strip_ansi(result.output)


def test_setup_help_includes_skip_image_verify() -> None:
    """The setup command exposes a --skip-image-verify flag."""
    result = runner.invoke(app, ["setup", "--help"])
    assert result.exit_code == 0
    assert "--skip-image-verify" in _strip_ansi(result.output)


def test_research_report_command_registered() -> None:
    """The todo-17 named surface `corpus-kb research report` exists."""
    result = runner.invoke(app, ["research", "--help"])
    assert result.exit_code == 0
    assert "report" in _strip_ansi(result.output)


def test_research_report_help_exposes_level_and_json() -> None:
    """The report command exposes the presentation-level and JSON flags."""
    result = runner.invoke(app, ["research", "report", "--help"])
    assert result.exit_code == 0
    output = _strip_ansi(result.output)
    assert "--level" in output
    assert "--json" in output


def test_version_flag_prints_single_sourced_version() -> None:
    """``corpus-kb --version`` prints the package version and exits 0."""
    from corpus_kb import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__


def test_version_flag_works_without_subcommand() -> None:
    """``--version`` exits before the no-subcommand help branch runs."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "Usage" not in result.output


def test_start_help_includes_host() -> None:
    """The start command exposes a --host flag for LAN binds (NAS compose)."""
    result = runner.invoke(app, ["start", "--help"])
    assert result.exit_code == 0
    assert "--host" in _strip_ansi(result.output)


def test_start_passes_host_to_server_main(monkeypatch: Any) -> None:
    """--host travels into the server argv unchanged."""
    import corpus_kb.server_wiring as wiring

    seen: dict[str, list[str]] = {}

    def fake_main(argv: list[str] | None = None) -> None:
        seen["argv"] = list(argv or [])

    monkeypatch.setattr(wiring, "main", fake_main)
    result = runner.invoke(
        app,
        ["start", "--transport", "http", "--port", "8010", "--host", "0.0.0.0"],
    )
    assert result.exit_code == 0
    assert seen["argv"] == ["--transport", "http", "--port", "8010", "--host", "0.0.0.0"]
