"""Offline tests for the research cycle (todo 20): schema pin, hard floor,
gate evaluators, resume points, and surface registration. NO database, NO
Ollama - the DB-bound E2E lives in test_cycle_fixture.py (r13 split).
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from corpus_kb.research import cycle as cycle_mod
from corpus_kb.research import cycle_events as ev
from corpus_kb.research import cycle_gates as gates
from corpus_kb.research import cycle_state as state
from corpus_kb.research import guide_copy
from corpus_kb.research.cycle_events import bounded_receipt, validate_cycle_event

# ---------------------------------------------------------------------------
# Pinned event schema
# ---------------------------------------------------------------------------


def test_event_names_and_required_fields_pinned() -> None:
    assert (
        frozenset(
            {
                "cycle_started",
                "stage_started",
                "stage_completed",
                "stage_unavailable",
                "gate_raised",
                "cycle_halted",
                "awaiting_approval",
                "approval_denied",
                "cycle_completed",
            }
        )
        == ev.EVENT_NAMES
    )
    assert ev.EVENT_REQUIRED["cycle_halted"] == ("run_id", "gate", "exit_code", "action")


def test_cycle_event_validates_known_and_unknown() -> None:
    ok = ev.cycle_event("stage_started", run_id="r", stage="ingest")
    assert ok["stage"] == "ingest"
    with pytest.raises(ValueError, match="unknown event name"):
        ev.cycle_event("nope", run_id="r")
    with pytest.raises(ValueError, match="missing field"):
        ev.cycle_event("stage_started", run_id="r")


def test_validate_cycle_event_reports_missing_fields() -> None:
    problems = validate_cycle_event({"event": "cycle_halted", "run_id": "r"})
    assert set(problems) == {
        "missing field: gate",
        "missing field: exit_code",
        "missing field: action",
    }


def test_recorder_accumulates_and_fails_loud() -> None:
    recorder = ev.CycleRecorder(json_output=False)
    recorder.emit("stage_started", run_id="r", stage="ingest")
    assert len(recorder.events) == 1
    with pytest.raises(ValueError):
        recorder.emit("stage_started", run_id="r")


def test_bounded_receipt_keeps_scalars_and_bounds_dicts() -> None:
    receipt: dict[str, Any] = {
        "status": "success",
        "run_id": "r1",
        "n_units": 5,
        "review": 2,
        "per_unit": {str(i): i for i in range(50)},
        "tags": ["a", "b"],
    }
    bounded = bounded_receipt(receipt)
    assert bounded["status"] == "success"
    assert isinstance(bounded["per_unit"], dict) and len(cast(dict, bounded["per_unit"])) <= 12
    assert bounded["tags"] == ["a", "b"]


# ---------------------------------------------------------------------------
# Hard floor + halt configuration
# ---------------------------------------------------------------------------


def test_hard_floor_survives_config_removal() -> None:
    assert frozenset({"codebook_promotion"}) == gates.HARD_FLOOR_GATES
    halt_set = gates.effective_halt_on(["drift_alarm"])
    assert "codebook_promotion" in halt_set
    assert "drift_alarm" in halt_set
    assert "codebook_promotion" in gates.effective_halt_on([])


def test_default_config_halt_on_matches_gate_module() -> None:
    from corpus_kb.config import get_default_config

    research_cfg = cast(dict, get_default_config()["research"])
    cycle_cfg = cast(dict, research_cfg["cycle"])
    assert list(cycle_cfg["halt_on"]) == list(gates.DEFAULT_HALT_ON)
    halt_set = gates.effective_halt_on(cycle_cfg["halt_on"])
    assert halt_set >= gates.ALL_GATES


def test_unknown_gate_name_rejected() -> None:
    with pytest.raises(gates.UnknownGateError, match="not_a_gate"):
        gates.effective_halt_on(["not_a_gate"])


def test_gate_exit_codes_distinct_and_pinned() -> None:
    codes = sorted(gates.GATE_EXIT_CODES.values())
    assert len(set(codes)) == len(codes)
    assert gates.GATE_EXIT_CODES["codebook_promotion"] == 10
    assert gates.GATE_EXIT_CODES["human_parity_breach"] == 16
    assert gates.EXIT_APPROVAL_DENIED == 20
    assert gates.EXIT_AWAITING_APPROVAL == 21
    assert gates.EXIT_UNAVAILABLE == 2


# ---------------------------------------------------------------------------
# Gate evaluators (pure)
# ---------------------------------------------------------------------------


def test_gate_from_inductive_fires_on_dbcv_drop() -> None:
    assert gates.gate_from_inductive({"dbcv_drop_flag": False}) == []
    assert gates.gate_from_inductive({}) == []
    findings = gates.gate_from_inductive({"dbcv_drop_flag": True, "dbcv_baseline": 0.5})
    assert [f.gate for f in findings] == ["drift_alarm"]


def test_gate_pending_proposals_is_the_floor_gate() -> None:
    assert gates.gate_pending_proposals(0, "run") is None
    finding = gates.gate_pending_proposals(3, "run-1")
    assert finding is not None and finding.gate == "codebook_promotion"
    assert finding.detail["pending_proposals"] == 3


def _code(code_id: str, theory: dict[str, object]) -> dict[str, object]:
    return {"code_id": code_id, "theory": theory}


def test_gate_from_theories_flags_unreliable_and_interpretive() -> None:
    codes = [
        _code("a", {"thresholds": {"threshold_unreliable": True}}),
        _code("b", {"thresholds": {"threshold_unreliable": False}, "is_interpretive": True}),
        _code("c", {"thresholds": {"threshold_unreliable": False}}),
    ]
    by_gate = {f.gate: f for f in gates.gate_from_theories(codes)}
    assert set(by_gate) == {"threshold_unreliable", "interpretive_code_review"}
    assert by_gate["threshold_unreliable"].detail["codes"] == ["a"]
    assert by_gate["interpretive_code_review"].detail["codes"] == ["b"]
    assert gates.gate_from_theories([_code("d", {})]) == []


def test_gate_from_deductive_maps_review_and_conformal() -> None:
    assert gates.gate_from_deductive({"review": 0}, None) == []
    findings = gates.gate_from_deductive({"review": 4}, None)
    assert [f.gate for f in findings] == ["gray_zone_escalation"]
    checkpoint = {"conformal": {"nominal_coverage": 0.9, "empirical_coverage": 0.85}}
    findings = gates.gate_from_deductive({"review": 0}, checkpoint)
    assert [f.gate for f in findings] == ["gray_zone_escalation"]
    assert "conformal" in findings[0].reason
    ok = gates.gate_from_deductive(
        {"review": 0}, {"conformal": {"nominal_coverage": 0.9, "empirical_coverage": 0.95}}
    )
    assert ok == []


def test_gate_from_report_maps_overlap_and_parity() -> None:
    assert gates.gate_from_report({}) == []
    findings = gates.gate_from_report({"overlap": {"flagged_pairs": [{"a": 1}]}})
    assert [f.gate for f in findings] == ["overlap_conflict"]
    findings = gates.gate_from_report({"keywords": {"conflicts": [{"kw": "x"}]}})
    assert [f.gate for f in findings] == ["overlap_conflict"]
    findings = gates.gate_from_report({"g3_audit": {"halt_gate": "human_parity_breach"}})
    assert [f.gate for f in findings] == ["human_parity_breach"]
    findings = gates.gate_from_report({"g3_audit": {"parity_breach": True}})
    assert [f.gate for f in findings] == ["human_parity_breach"]
    findings = gates.gate_from_report({"g3_audit": {"parity_breach": False, "halt_gate": None}})
    assert findings == []


# ---------------------------------------------------------------------------
# Stage list + resume points
# ---------------------------------------------------------------------------


def test_stage_chain_pinned() -> None:
    assert cycle_mod.STAGES == (
        "ingest",
        "inductive",
        "deductive",
        "keywords",
        "report",
        "notebook",
    )


def test_stages_after_matches_resume_semantics() -> None:
    assert cycle_mod.stages_after(None) == cycle_mod.STAGES
    assert cycle_mod.stages_after("ingest") == cycle_mod.STAGES[1:]
    assert cycle_mod.stages_after("report") == ("notebook",)
    assert cycle_mod.stages_after("notebook") == ()
    assert cycle_mod.stages_after("bogus") == cycle_mod.STAGES


def test_resume_point_from_checkpoint_kinds() -> None:
    assert state.resume_point(None) == (None, None)
    assert state.resume_point({"checkpoint": {"kind": "stage_complete", "stage": "ingest"}}) == (
        "ingest",
        None,
    )
    assert state.resume_point(
        {"checkpoint": {"kind": "gate_halt", "gate": "codebook_promotion", "stage": "inductive"}}
    ) == ("inductive", "codebook_promotion")
    assert state.resume_point(
        {"checkpoint": {"kind": "approval_denied", "stage": "deductive"}}
    ) == ("deductive", None)
    assert state.resume_point({"checkpoint": {"kind": "cycle_complete"}}) == (None, None)
    assert state.resume_point({"checkpoint": {}}) == (None, None)


def test_halted_gate_respects_halt_set_and_suppression() -> None:
    findings = [
        gates.GateFinding("drift_alarm", "r1"),
        gates.GateFinding("codebook_promotion", "r2"),
    ]
    halt = cycle_mod._halted_gate(findings, gates.ALL_GATES, None)
    assert halt is not None and halt.gate == "drift_alarm"
    halt = cycle_mod._halted_gate(findings, frozenset({"codebook_promotion"}), "codebook_promotion")
    assert halt is None
    halt = cycle_mod._halted_gate(findings, frozenset({"codebook_promotion"}), None)
    assert halt is not None and halt.gate == "codebook_promotion"


# ---------------------------------------------------------------------------
# Taught gates (guide_copy) + surfaces
# ---------------------------------------------------------------------------


def test_guide_copy_has_taught_copy_for_every_gate() -> None:
    from corpus_kb.research.cycle_render import GATE_GUIDE

    assert set(GATE_GUIDE) == gates.ALL_GATES
    for gate, (taught, look, action) in GATE_GUIDE.items():
        assert taught and look and action, gate
        assert "AWAITING HUMAN" not in taught  # header is templated separately


def test_promotion_gate_taught_block_covers_consequences() -> None:
    assert "If you promote" in guide_copy.GATE_CODEBOOK_PROMOTION_PROMOTE_CONSEQUENCE
    assert "If you skip" in guide_copy.GATE_CODEBOOK_PROMOTION_SKIP_CONSEQUENCE
    assert "codebook promote" in guide_copy.GATE_CODEBOOK_PROMOTION_ACTION
    assert guide_copy.CYCLE_HALT_HEADER.format(gate="x") == "AWAITING HUMAN: x"


def test_cycle_constants_reference_real_docs() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    docs = {p.name for p in (root / "docs").glob("*.md")}
    for name in (
        guide_copy.CYCLE_DOC,
        guide_copy.CYCLE_STAGE_INGEST_DOC,
        guide_copy.CYCLE_STAGE_INDUCTIVE_DOC,
        guide_copy.CYCLE_STAGE_DEDUCTIVE_DOC,
        guide_copy.CYCLE_STAGE_KEYWORDS_DOC,
        guide_copy.CYCLE_STAGE_REPORT_DOC,
        guide_copy.CYCLE_STAGE_NOTEBOOK_DOC,
    ):
        rel = Path(name.split("#")[0]).name
        assert rel in docs, name
    assert guide_copy.CYCLE_DOC.startswith("docs/research.md#")
    research_md = (root / "docs" / "research.md").read_text(encoding="utf-8").lower()
    assert "research cycle" in research_md


def test_cycle_surface_registered_and_tool_name_valid() -> None:
    from corpus_kb import surface_registry as reg
    from corpus_kb._setup import validate_configs

    surfaces = {s.name: s for s in reg.SURFACES}
    cycle_surface = surfaces["research-cycle"]
    assert cycle_surface.cli_path == "research cycle"
    assert cycle_surface.mcp_tool == "research_cycle"
    assert cycle_surface.mcp_tool in validate_configs.VALID_TOOL_NAMES
    wrappers = {w.name for w in reg.ACTIVE_WRAPPERS}
    assert "corpus-research-cycle" in wrappers


def test_cycle_cli_command_registered() -> None:
    from corpus_kb import cli

    names = [c.name for c in cli.research_app.registered_commands]
    assert "cycle" in names


def test_mcp_tool_status_mapping() -> None:
    from corpus_kb.research.cycle_gates import (
        EXIT_APPROVAL_DENIED,
        EXIT_AWAITING_APPROVAL,
        EXIT_UNAVAILABLE,
    )
    from corpus_kb.tools import cycle_tools

    assert cycle_tools._status_for(0, False) == "completed"
    assert cycle_tools._status_for(EXIT_AWAITING_APPROVAL, False) == "awaiting_approval"
    assert cycle_tools._status_for(EXIT_APPROVAL_DENIED, False) == "approval_denied"
    assert cycle_tools._status_for(EXIT_UNAVAILABLE, False) == "unavailable"
    assert cycle_tools._status_for(10, True) == "halted"
    assert cycle_tools._status_for(10, False) == "stopped"


def test_cycle_embedder_preflight_copy_names_the_fix() -> None:
    assert "embedding.provider" in guide_copy.CYCLE_NO_EMBEDDINGS
    assert "qwen3-embedding" in guide_copy.CYCLE_NO_EMBEDDINGS


def test_run_cycle_rejects_unknown_mode() -> None:
    import asyncio

    with pytest.raises(ValueError, match="unknown cycle mode"):
        asyncio.run(
            cycle_mod.run_cycle(
                cast(Any, object()),
                cast(Any, "00000000-0000-0000-0000-000000000001"),
                mode="sideways",
            )
        )
