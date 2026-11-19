"""Tests for the doctor report printers (zero-drift split module).

Covers the printer branches the full-suite runs miss: the absent env
check, the bare DuckDB path, and a URL the parser refuses.
"""

from __future__ import annotations

from typing import Any

import pytest

from corpus_kb._setup import doctor_report
from corpus_kb._setup.doctor_report import (
    _database_display_name,
    print_doctor_report,
)


def _info(**overrides: Any) -> dict[str, Any]:
    """A doctor info dict with every key the text report reads."""
    info: dict[str, Any] = {
        "os": "Windows",
        "python": "3.11",
        "cpu_cores": 4,
        "ram_gb": 8,
        "vram_detected": False,
        "profile": "light",
        "postgres_ok": False,
        "postgres_msg": "no connection string",
        "ollama_ok": False,
        "ollama_msg": "down",
        "backend": "postgres",
        "recommended_model": "m",
    }
    info.update(overrides)
    return info


def test_print_doctor_report_without_env_check_skips_the_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No env check means no Configuration block; the rest stays intact."""
    print_doctor_report(_info(), env_check=None)
    out = capsys.readouterr().out
    assert "Configuration:" not in out
    assert "Recommended commands" in out


def test_database_display_name_passes_bare_duckdb_paths_through() -> None:
    """A DuckDB path without the DSN prefix is reported as written."""
    assert _database_display_name("duckdb", "C:\\data\\kb.duckdb") == "C:\\data\\kb.duckdb"


def test_database_display_name_returns_empty_on_a_bad_url() -> None:
    """A URL the parser refuses yields an empty name, never a crash."""
    assert _database_display_name("postgres", "http://[::1") == ""


def test_print_doctor_report_prints_fix_and_docs_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An env check with fix and doc text prints both hint lines."""
    from types import SimpleNamespace

    env_check = SimpleNamespace(
        name="CORPUS_KB_DATABASE_URL",
        status="warn",
        detail="set but unread",
        fix="unset the variable",
        doc="docs/ADMIN.md",
    )
    print_doctor_report(_info(), env_check=env_check)
    out = capsys.readouterr().out
    assert "fix: unset the variable" in out
    assert "docs: docs/ADMIN.md" in out


# Issue #464: the recommended-commands tail is backend- and layout-aware.


def test_doctor_tail_on_duckdb_names_the_duckdb_setup_path(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On duckdb the tail never shows the Postgres-framed lines."""
    monkeypatch.setattr(doctor_report, "_clone_layout", lambda: True)
    print_doctor_report(_info(backend="duckdb"), env_check=None)
    out = capsys.readouterr().out
    assert "corpus-kb setup --backend duckdb" in out
    assert "pip install -e .[dev]" not in out
    assert "  2. corpus-kb setup\n" not in out


def test_doctor_tail_on_a_wheel_install_drops_the_editable_pip_line(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An installed package (pipx, wheel) needs no editable pip install."""
    monkeypatch.setattr(doctor_report, "_clone_layout", lambda: False)
    print_doctor_report(_info(), env_check=None)
    out = capsys.readouterr().out
    assert "pip install -e .[dev]" not in out
    assert "  1. corpus-kb setup" in out
    assert "  2. ollama pull m" in out


def test_doctor_tail_in_a_clone_keeps_the_editable_pip_line(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checkout keeps the historical two-step Postgres advice."""
    monkeypatch.setattr(doctor_report, "_clone_layout", lambda: True)
    print_doctor_report(_info(), env_check=None)
    out = capsys.readouterr().out
    assert "  1. pip install -e .[dev]" in out
    assert "  2. corpus-kb setup" in out
    assert "  3. ollama pull m" in out
