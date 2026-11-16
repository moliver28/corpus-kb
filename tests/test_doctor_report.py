"""Tests for the doctor report printers (zero-drift split module).

Covers the printer branches the full-suite runs miss: the absent env
check, the bare DuckDB path, and a URL the parser refuses.
"""

from __future__ import annotations

from typing import Any

import pytest

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
