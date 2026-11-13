"""Row validation: stance enums, unit ids, evidence, quotes (issue #355).

Rows that fail any check store as ``unverified``. Near misses pass but
get logged. Stance enums: ``rq`` is free text or the no-evidence
sentence; ``h`` is Supports, Challenges, Mixed, No direct evidence;
``a`` is Holds, Breaks, Untested.
"""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import uxr_rows  # noqa: E402
import uxr_store  # noqa: E402

UNIT_TEXT = "The export button worked on the second try, but the icon was unclear."


def open_store(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    conn = uxr_store.connect("ux1")
    uxr_store.save_study(
        conn,
        spec_id="0123456789abcdef",
        slug="ux1",
        ticket="UX1-275",
        questions=[
            {"id": "RQ1", "kind": "rq", "position": 1, "label": "What breaks?"},
            {"id": "H1", "kind": "h", "position": 1, "label": "Export works"},
            {"id": "A1", "kind": "a", "position": 1, "label": "Nav holds"},
        ],
    )
    return conn


def row(**over: object) -> dict:
    base = {
        "session": "p1",
        "unit_id": "H1",
        "stance": "Supports",
        "quote": "the export button worked",
        "unit_text": UNIT_TEXT,
        "rationale": "the participant said it worked",
    }
    base.update(over)
    return base


def test_unit_kind_from_the_prefix() -> None:
    assert uxr_rows.unit_kind("RQ1") == "rq"
    assert uxr_rows.unit_kind("H2") == "h"
    assert uxr_rows.unit_kind("A3") == "a"
    assert uxr_rows.unit_kind("X1") is None
    assert uxr_rows.unit_kind("H") is None


def test_stance_rules_per_kind() -> None:
    assert uxr_rows.stance_ok("h", "Supports")
    assert uxr_rows.stance_ok("h", "No direct evidence")
    assert not uxr_rows.stance_ok("h", "Holds")
    assert uxr_rows.stance_ok("a", "Untested")
    assert not uxr_rows.stance_ok("a", "Supports")
    assert uxr_rows.stance_ok("rq", "anything the analyst wrote")
    assert uxr_rows.stance_ok("rq", uxr_rows.NO_EVIDENCE_RQ)
    assert not uxr_rows.stance_ok("rq", "")


def test_good_row_verifies_exact(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    report = uxr_rows.validate_rows(conn, [row()])
    entry = report["rows"][0]
    assert entry["ok"] is True
    assert entry["verified"] is True
    assert entry["quote_class"] == "exact"
    assert report["stored"] == 1
    assert report["unverified"] == 0


def test_near_miss_verifies_but_is_logged(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    near = row(quote="the export button worked on the second test")
    report = uxr_rows.validate_rows(conn, [near])
    entry = report["rows"][0]
    assert entry["quote_class"] == "near_miss"
    assert entry["verified"] is True
    assert report["near_misses"], "the near miss must be logged"


def test_failed_quote_stores_unverified(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    report = uxr_rows.validate_rows(conn, [row(quote="the moon was made of cheese")])
    entry = report["rows"][0]
    assert entry["ok"] is False
    assert entry["verified"] is False
    assert report["unverified"] == 1


def test_unknown_unit_id_and_bad_stance_fail(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    report = uxr_rows.validate_rows(
        conn, [row(unit_id="H9"), row(stance="Holds")]
    )
    problems = [p for r in report["rows"] for p in r["problems"]]
    assert any("unit id" in p for p in problems)
    assert any("stance" in p for p in problems)
    assert report["unverified"] == 2


def test_store_false_reports_but_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    conn = open_store(tmp_path, monkeypatch)
    report = uxr_rows.validate_rows(conn, [row()], store=False)
    assert report["rows"][0]["ok"] is True
    assert uxr_store.rows(conn) == []


def test_store_true_writes_failed_rows_as_unverified(
    tmp_path: Path, monkeypatch
) -> None:
    conn = open_store(tmp_path, monkeypatch)
    uxr_rows.validate_rows(
        conn, [row(), row(quote="totally invented words here")], store=True
    )
    saved = uxr_store.rows(conn)
    assert len(saved) == 2
    verified = [r for r in saved if r["verified"] == 1]
    unverified = [r for r in saved if r["verified"] == 0]
    assert len(verified) == 1
    assert len(unverified) == 1
