"""Skill table checks (uxr registry spine, plan Tasks 1 and 3).

Task 1: corpus-analyze shows the route-then-run flow. It names
``analysis-route`` and ``run_hint``, and it no longer ends in a dead
"offer to run the method" line.

Task 3: corpus-uxr carries a method support pass that names the four
registry ops and states that method output advises while the
researcher decides.

The gate for the corpus-research op table lives engine-side in
``tests/test_plugin_skill_table_gate.py``.

Stdlib only.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]

ANALYZE_SKILL = (PLUGIN / "skills/corpus-analyze/SKILL.md").read_text(encoding="utf-8")
ANALYZE_COMMAND = (PLUGIN / "commands/corpus-analyze.md").read_text(encoding="utf-8")
UXR_SKILL = (PLUGIN / "skills/corpus-uxr/SKILL.md").read_text(encoding="utf-8")


def test_analyze_skill_names_the_router() -> None:
    assert "analysis-route" in ANALYZE_SKILL


def test_analyze_skill_presents_run_hint_rows() -> None:
    assert "run_hint" in ANALYZE_SKILL
    for key in ("method_id", "name", "why"):
        assert key in ANALYZE_SKILL


def test_analyze_skill_has_no_dead_end_offer() -> None:
    assert "offer to run the method" not in ANALYZE_SKILL.lower()


def test_analyze_skill_renders_degraded_mode() -> None:
    assert "degraded" in ANALYZE_SKILL.lower()


def test_analyze_command_follows_the_router() -> None:
    assert "analysis-route" in ANALYZE_COMMAND


def test_uxr_skill_names_the_four_method_ops() -> None:
    for op in (
        "keyphrases-extract",
        "clusters-inspect",
        "sentiment-screen",
        "contradictions-screen",
    ):
        assert op in UXR_SKILL, op


def test_uxr_skill_states_method_output_advises() -> None:
    assert "advises" in UXR_SKILL.lower()
