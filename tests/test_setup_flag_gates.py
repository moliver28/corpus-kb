"""The setup Postgres-only flags are typed-refused on a DuckDB target.

Issue #527 (G15): ``--build-local`` and ``--skip-image-verify`` shape the
Postgres docker stack only and used to be silent no-ops under
``--backend duckdb``. The refusal raises before any work starts, so the
refusal tests need no Docker and no database.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from corpus_kb._setup.backend_flag_gates import refuse_pg_only_setup_flags
from corpus_kb.cli import app
from corpus_kb.errors import ConfigError

runner = CliRunner()


def _assert_refused(result: Any, flag: str) -> None:
    assert result.exit_code == 3, result.output
    assert flag in result.output
    assert "Postgres" in result.output
    assert "DuckDB" in result.output


def test_setup_duck_refuses_build_local() -> None:
    result = runner.invoke(app, ["setup", "--backend", "duckdb", "--build-local"])
    _assert_refused(result, "--build-local")


def test_setup_duck_refuses_skip_image_verify() -> None:
    result = runner.invoke(app, ["setup", "--backend", "duckdb", "--skip-image-verify"])
    _assert_refused(result, "--skip-image-verify")


def test_setup_pg_accepts_both() -> None:
    """Postgres targets pass the gate; the dry run prints and exits clean."""
    refuse_pg_only_setup_flags("postgres", build_local=True, skip_image_verify=True)  # no raise
    result = runner.invoke(app, ["setup", "--dry-run", "--build-local", "--skip-image-verify"])
    assert result.exit_code == 0, result.output
    assert "ERROR" not in result.output


def test_refusal_message_under_20_words() -> None:
    """Every refusal sentence stays at or under the 20-word STE cap."""
    for backend_flag in ("--build-local", "--skip-image-verify"):
        kwargs = (
            {"build_local": True, "skip_image_verify": False}
            if backend_flag == "--build-local"
            else {"build_local": False, "skip_image_verify": True}
        )
        with pytest.raises(ConfigError) as excinfo:
            refuse_pg_only_setup_flags("duckdb", **kwargs)
        message = str(excinfo.value)
        remediation = excinfo.value.remediation
        for sentence in message.split(". ") + remediation.split(". "):
            assert len(sentence.split()) <= 20, sentence
        assert backend_flag in message
