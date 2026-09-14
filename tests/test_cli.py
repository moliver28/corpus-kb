"""Tests for the ``corpus-kb`` Typer CLI."""

from __future__ import annotations

from typer.testing import CliRunner

from corpus_kb.cli import app

runner = CliRunner()


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
