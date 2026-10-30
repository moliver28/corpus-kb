"""The ``textbook scan`` CLI leg: the table, --json, and typed exits.

The scan core runs on real bytes when pypdf exists (the [scan] extra);
those tests skip without it. The typed exit paths (missing path, missing
pypdf) run everywhere.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from corpus_kb.cli import app
from corpus_kb.cli.cli_textbook import _FontChatterFilter
from tests.test_textbook_scan import INDEX_PAGE, TEXTBOOK_PAGE, _book_texts, _write_pdf

runner = CliRunner()


def _rich_book(tmp_path: Path) -> Path:
    pytest.importorskip("pypdf")
    return _write_pdf(
        tmp_path / "book.pdf",
        [*_book_texts(130, TEXTBOOK_PAGE)[:-1], INDEX_PAGE],
        outline=[0, 40, 80],
    )


def test_scan_prints_the_deterministic_table(tmp_path: Path) -> None:
    book = _rich_book(tmp_path)
    result = runner.invoke(app, ["textbook", "scan", str(tmp_path)])
    assert result.exit_code == 0
    assert "textbook scan: 1 pdf files, 1 candidates" in result.output
    line = next(line for line in result.output.splitlines() if str(book) in line)
    score = float(line.split()[0])
    assert score > 0.5
    assert "130 pages" in line


def test_scan_json_envelope(tmp_path: Path) -> None:
    book = _rich_book(tmp_path)
    result = runner.invoke(app, ["textbook", "scan", str(tmp_path), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "success"
    assert payload["count"] == 1
    row = payload["results"][0]
    assert row["path"] == str(book)
    assert row["candidate"] is True
    assert set(row["signals"]) == {
        "text_layer",
        "outline",
        "definitions",
        "xrefs",
        "hedging",
        "tables",
        "index_glossary",
        "overlap",
    }


def test_scan_reports_a_below_band_pdf_without_a_score(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    _write_pdf(tmp_path / "receipt.pdf", ["TOTAL 4.00"])
    result = runner.invoke(app, ["textbook", "scan", str(tmp_path)])
    assert result.exit_code == 0
    assert "1 pdf files, 0 candidates" in result.output
    assert "(not a candidate)" in result.output


def test_scan_missing_path_exits_one(tmp_path: Path) -> None:
    result = runner.invoke(app, ["textbook", "scan", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "ERROR" in result.output
    assert "does not exist" in result.output


def test_scan_missing_pypdf_exits_three(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Bytes do not matter: the lazy import fails before the file opens.
    (tmp_path / "book.pdf").write_bytes(b"%PDF-1.4 not really")
    monkeypatch.setitem(sys.modules, "pypdf", None)
    result = runner.invoke(app, ["textbook", "scan", str(tmp_path)])
    assert result.exit_code == 3
    assert "ERROR" in result.output
    assert "[scan]" in result.output


def test_font_chatter_filter_drops_fonttools_records() -> None:
    chatter = logging.LogRecord(
        "pypdf", logging.WARNING, "p.py", 1, "fontTools not found: %s", ("x",), None
    )
    normal = logging.LogRecord("pypdf", logging.WARNING, "p.py", 1, "page 3 malformed", None, None)
    gate: Callable[[logging.LogRecord], bool] = _FontChatterFilter()
    assert gate.filter(chatter) is False
    assert gate.filter(normal) is True


def test_cli_registers_the_textbook_group() -> None:
    result = runner.invoke(app, ["textbook", "--help"])
    assert result.exit_code == 0
    assert "scan" in result.output
