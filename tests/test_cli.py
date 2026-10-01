"""Tests for the ``corpus-kb`` Typer CLI."""

from __future__ import annotations

import re

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
