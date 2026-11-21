"""Coded-evidence merge and advisory assists (plan Task 5).

``uxr_merge.merge_coded_evidence`` appends the coding_export rows as a
marked section to the themes text. It is idempotent: a second merge
replaces its own section and never touches the model narrative.
``quote_second_opinion`` asks the engine's search op for an advisory
second opinion; it returns None when the op errors. The contract
default path runs the rollup as its own evidence with an honest
degraded note and exits 0. Stdlib only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = str(Path(__file__).resolve().parents[1])
if PLUGIN not in sys.path:
    sys.path.insert(0, PLUGIN)

import uxr_analysis  # noqa: E402
import uxr_merge  # noqa: E402

ROW = {
    "chunk_id": "c1",
    "code_id": "trust",
    "confidence": 0.87,
    "decided_at": "2026-11-20T10:00:00",
    "text": "The participant expects the vendor to deliver.",
    "document_source": "interviews/p1.vtt",
}

NARRATIVE = "The sessions show trust grows after onboarding.\nSecond line.\n"


def test_merge_is_idempotent() -> None:
    once = uxr_merge.merge_coded_evidence(NARRATIVE, [ROW])
    twice = uxr_merge.merge_coded_evidence(once, [ROW])
    assert once == twice


def test_merge_keeps_narrative_verbatim() -> None:
    merged = uxr_merge.merge_coded_evidence(NARRATIVE, [ROW])
    assert merged.startswith(NARRATIVE)
    assert "trust grows after onboarding" in merged
    assert uxr_merge.EVIDENCE_BEGIN in merged


def test_merge_empty_rows_changes_nothing() -> None:
    assert uxr_merge.merge_coded_evidence(NARRATIVE, []) == NARRATIVE


def test_merge_renders_row_fields() -> None:
    merged = uxr_merge.merge_coded_evidence(NARRATIVE, [ROW])
    assert "trust" in merged
    assert "interviews/p1.vtt" in merged
    assert "0.87" in merged


def test_merge_rows_sort_deterministically() -> None:
    row_b = {**ROW, "code_id": "agency"}
    merged = uxr_merge.merge_coded_evidence(NARRATIVE, [ROW, row_b])
    assert merged.index("- [agency]") < merged.index("- [trust]")


def test_second_opinion_none_on_op_error() -> None:
    def sender(name: str, pairs: list[str]) -> dict:
        raise RuntimeError("engine down")

    assert uxr_merge.quote_second_opinion("a quote", sender) is None


def test_second_opinion_returns_envelope() -> None:
    def sender(name: str, pairs: list[str]) -> dict:
        assert name == "search"
        return {"status": "success", "result": [REALISTIC_HIT]}

    out = uxr_merge.quote_second_opinion("a quote", sender)
    assert out is not None
    assert out["advisory"] is True
    assert out["result"] == [REALISTIC_HIT]


# Search hits carry ``source``/``file_path`` (domain models), not
# ``document_source``; the printed name must be the file, never a UUID.
REALISTIC_HIT = {
    "source": "interviews/p1.vtt",
    "file_path": "interviews/p1.vtt",
    "chunk_id": "0f0e0d0c-1a2b-3c4d-5e6f-708090a0b0c0",
}


def test_second_opinion_prints_file_name_not_uuid(monkeypatch, capsys) -> None:
    import uxr_commands

    monkeypatch.setattr(
        uxr_commands.uxr_merge,
        "search_sender",
        lambda slug: lambda name, pairs: {},
    )
    monkeypatch.setattr(
        uxr_commands.uxr_merge,
        "quote_second_opinion",
        lambda quote, sender: {"result": [REALISTIC_HIT]},
    )

    class Args:
        quote = "a quote"
        study_slug = "q3"

    uxr_commands._print_second_opinion(Args())
    out = capsys.readouterr().out
    assert "interviews/p1.vtt" in out
    assert "0f0e0d0c" not in out


def test_second_opinion_skips_empty_quote() -> None:
    called: list = []
    assert uxr_merge.quote_second_opinion("   ", lambda *a: called.append(a)) is None
    assert called == []


# ---------------------------------------------------------------------------
# Contract step: the degraded default path exits 0 with an honest note
# ---------------------------------------------------------------------------


def _isolate_store(monkeypatch, tmp_path: Path, rows: list[dict] | None = None) -> None:
    monkeypatch.setattr(uxr_analysis.uxr_store, "connect", lambda slug: object())
    monkeypatch.setattr(
        uxr_analysis.uxr_store, "rows", lambda conn: rows if rows is not None else []
    )
    monkeypatch.setattr(uxr_analysis.uxr_rollup, "researcher_from_dir", lambda root: {})


VERIFIED_ROW = {
    "session": "p1",
    "unit_id": "RQ1",
    "stance": "Supports",
    "quote": "q",
    "quote_class": "exact",
    "rationale": "r",
    "verified": True,
}


class Args:
    evidence_file = None
    study_slug = "q3"
    dir = None


def test_contract_without_report_tool_exits_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_UXR_REPORT_TOOL", raising=False)
    assert uxr_analysis.cmd_uxr_contract_test(Args()) == 0
    out = capsys.readouterr().out
    assert "degraded" in out.lower()


def test_contract_parity_difference_still_exits_one(tmp_path: Path, monkeypatch) -> None:
    _isolate_store(monkeypatch, tmp_path, rows=[VERIFIED_ROW])
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "units": {"RQ1": {"Supports": 99}},
                "researcher": {},
                "totals": {"verified": 0, "unverified": 0, "share": 0.0},
            }
        ),
        encoding="utf-8",
    )
    args = Args()
    args.evidence_file = str(evidence)
    assert uxr_analysis.cmd_uxr_contract_test(args) == 1


def test_contract_parity_pass_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    _isolate_store(monkeypatch, tmp_path)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "units": {},
                "researcher": {},
                "totals": {"verified": 0, "unverified": 0, "share": 0.0},
            }
        ),
        encoding="utf-8",
    )
    args = Args()
    args.evidence_file = str(evidence)
    assert uxr_analysis.cmd_uxr_contract_test(args) == 0
