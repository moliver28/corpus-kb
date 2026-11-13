"""Rollup counting, researcher lines, and contract parity (issue #355).

The rollup counts stances per question from verified rows only, then
adds researcher lines from ``interviews/*-summary.md``. A researcher
line takes precedence over the model rows for its question. The
contract test compares the rollup with the report tool's
``evidence-weight`` output and exits 1 on any difference.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import uxr_rollup  # noqa: E402


def verified(session: str, unit: str, stance: str) -> dict:
    return {
        "session": session,
        "unit_id": unit,
        "stance": stance,
        "quote": "q",
        "quote_class": "exact",
        "rationale": "r",
        "verified": True,
    }


def unverified(session: str, unit: str, stance: str) -> dict:
    row = verified(session, unit, stance)
    row["verified"] = False
    return row


# ---------------------------------------------------------------------------
# Counts and ordering
# ---------------------------------------------------------------------------


def test_counts_use_verified_rows_only() -> None:
    rows = [
        verified("p1", "H1", "Supports"),
        verified("p2", "H1", "Supports"),
        verified("p3", "H1", "Mixed"),
        unverified("p4", "H1", "Challenges"),
    ]
    counts = uxr_rollup.counts(rows)
    assert counts == {"H1": {"Supports": 2, "Mixed": 1}}


def test_unit_order_is_rq_then_h_then_a() -> None:
    rows = [
        verified("p1", "A1", "Holds"),
        verified("p1", "H1", "Supports"),
        verified("p1", "RQ1", "one finding"),
    ]
    counts = uxr_rollup.counts(rows)
    assert list(counts) == ["RQ1", "H1", "A1"]


# ---------------------------------------------------------------------------
# Researcher lines
# ---------------------------------------------------------------------------


def test_researcher_lines_come_from_summaries_outside_blocks() -> None:
    text = (
        "Notes from the analyst.\n"
        "H1: Supports (I saw it myself).\n"
        "corpus:begin\n"
        "- H2: Challenges 2\n"
        "corpus:end\n"
        "A1: Untested.\n"
    )
    lines = uxr_rollup.researcher_lines(text)
    assert lines == {"H1": "Supports (I saw it myself).", "A1": "Untested."}


def test_researcher_line_takes_precedence_over_model_rows() -> None:
    rows = [verified("p1", "H1", "Supports"), verified("p2", "H1", "Challenges")]
    researcher = {"H1": "No direct evidence (the session was cut short)."}
    report = uxr_rollup.build(rows, researcher)
    assert report["units"]["H1"] == {"Supports": 1, "Challenges": 1}
    assert report["researcher"]["H1"] == (
        "No direct evidence (the session was cut short)."
    )


def test_researcher_lines_merge_first_file_wins(tmp_path: Path) -> None:
    root = tmp_path / "study"
    (root / "interviews").mkdir(parents=True)
    (root / "interviews" / "a-summary.md").write_text("H1: Supports.\n", encoding="utf-8")
    (root / "interviews" / "b-summary.md").write_text("H1: Mixed.\n", encoding="utf-8")
    merged = uxr_rollup.researcher_from_dir(root)
    assert merged == {"H1": "Supports."}


# ---------------------------------------------------------------------------
# Unverified share
# ---------------------------------------------------------------------------


def test_unverified_share_over_limit() -> None:
    rows = [verified("p1", "H1", "Supports"), unverified("p2", "H1", "Mixed")]
    report = uxr_rollup.build(rows, {})
    assert uxr_rollup.over_limit(report, 0.1) is True
    assert uxr_rollup.over_limit(report, 0.6) is False


def test_empty_rollup_never_exceeds_the_limit() -> None:
    report = uxr_rollup.build([], {})
    assert uxr_rollup.over_limit(report, 0.1) is False


# ---------------------------------------------------------------------------
# Contract parity
# ---------------------------------------------------------------------------


def rollup_fixture() -> dict:
    rows = [
        verified("p1", "H1", "Supports"),
        verified("p2", "H1", "Supports"),
        verified("p1", "RQ1", "the export flow needs a undo"),
    ]
    return uxr_rollup.rollup_json(uxr_rollup.build(rows, {}))


def test_contract_diff_passes_on_equal_json() -> None:
    ours = rollup_fixture()
    assert uxr_rollup.contract_diff(ours, json.loads(json.dumps(ours))) == []


def test_contract_diff_fails_on_any_difference() -> None:
    ours = rollup_fixture()
    theirs = json.loads(json.dumps(ours))
    theirs["units"]["H1"]["Supports"] = 99
    diffs = uxr_rollup.contract_diff(ours, theirs)
    assert diffs and "H1" in diffs[0]


def test_contract_diff_fails_on_a_missing_unit() -> None:
    ours = rollup_fixture()
    theirs = {"units": {}, "researcher": {}, "totals": ours["totals"]}
    diffs = uxr_rollup.contract_diff(ours, theirs)
    assert any("RQ1" in d for d in diffs)


def test_evidence_from_the_report_tool(tmp_path: Path) -> None:
    # The report tool reads the rollup on stdin and prints evidence-weight
    # JSON. The tool command line comes from the userConfig option.
    tool = tmp_path / "tool.py"
    tool.write_text(
        "import json, sys\n"
        "d = json.load(sys.stdin)\n"
        "d['units']['H1']['Supports'] = 99\n"
        "json.dump(d, sys.stdout)\n",
        encoding="utf-8",
    )
    command = f"{sys.executable} {tool}"
    ours = rollup_fixture()
    theirs = uxr_rollup.evidence_from_tool(command, ours)
    assert theirs["units"]["H1"]["Supports"] == 99
    diffs = uxr_rollup.contract_diff(ours, theirs)
    assert diffs


def test_evidence_tool_failure_raises() -> None:
    try:
        uxr_rollup.evidence_from_tool(f"{sys.executable} -c 'import sys; sys.exit(3)'", {})
    except uxr_rollup.ContractError:
        return
    raise AssertionError("a failed tool run must raise ContractError")


def test_tool_runs_from_a_temp_cwd(tmp_path: Path) -> None:
    tool = tmp_path / "tool.py"
    tool.write_text("print('{\"units\": {}}')\n", encoding="utf-8")
    theirs = uxr_rollup.evidence_from_tool(f"{sys.executable} {tool}", {})
    assert theirs == {"units": {}}
