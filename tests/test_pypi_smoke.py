"""The pypi-smoke gate's end-of-run summary line (issue #526).

Every run ends with one ``SUMMARY:`` line on stdout, and the same line
goes to ``GITHUB_STEP_SUMMARY`` when Actions sets it. A multi-day PyPI
outage is then visible in the Actions UI without failing PRs: the skip
exit code stays 0 (declared skip semantics, unchanged).
"""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from scripts import pypi_smoke


def test_skip_path_writes_a_summary_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The outage skip exits 0 and carries a SKIP summary line."""
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    def _dead() -> object:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(pypi_smoke, "_probe_once", _dead)
    with pytest.raises(SystemExit) as excinfo:
        pypi_smoke.main()
    assert excinfo.value.code == 0
    assert "SUMMARY: SKIP" in capsys.readouterr().out
    assert "SUMMARY: SKIP" in summary.read_text(encoding="utf-8")


def test_fail_path_writes_a_summary_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real failure exits 1 and carries a FAIL summary line."""
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(pypi_smoke, "_probe_pypi", lambda: None)

    def _boom(root: object, deadline: object) -> None:
        pypi_smoke._fail("venv create (exit 1)", "boom")

    monkeypatch.setattr(pypi_smoke, "_create_venv", _boom)
    with pytest.raises(SystemExit) as excinfo:
        pypi_smoke.main()
    assert excinfo.value.code == 1
    assert "SUMMARY: FAIL" in capsys.readouterr().out
    assert "SUMMARY: FAIL" in summary.read_text(encoding="utf-8")
