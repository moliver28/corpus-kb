"""Golden agreement table for the difficulty seed (issue 440).

The table pins each register row's graph depth, element interactivity,
and computed rung, plus the six stored pins that win. Frozen from the
3.3.0 register on master; the plan's Feasibility section is the record.
"""

from __future__ import annotations

import warnings
from typing import Any

from corpus_kb.analysis.method_register import MethodRecord
from corpus_kb.analysis.method_register_difficulty import (
    DECISION_WEIGHTS,
    computed_rung,
    difficulty_mismatches,
    difficulty_scores,
    element_interactivity,
    graph_depth,
)
from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.analysis.method_register_progression import DIFFICULTY_LEVELS

ALL_IDS = [f"{index:02d}" for index in range(1, 31)]

# The agreement table, one row per method: id, graph depth, element
# interactivity, computed rung. Frozen from the 3.3.0 register; the
# plan's Feasibility section is the record.
_GOLDEN_TABLE = """
01 0 3 advanced
02 0 2 novice
03 1 2 intermediate
04 1 1 novice
05 0 1 novice
06 1 1 novice
07 0 1 novice
08 0 2 novice
09 0 3 advanced
10 1 4 intermediate
11 2 4 intermediate
12 0 3 advanced
13 2 2 intermediate
14 3 2 intermediate
15 2 2 intermediate
16 3 2 intermediate
17 4 2 advanced
18 0 1 novice
19 0 3 advanced
20 5 3 advanced
21 0 3 advanced
22 0 3 advanced
23 0 2 novice
24 0 1 novice
25 0 1 novice
26 0 2 novice
27 2 3 intermediate
28 1 3 intermediate
29 0 1 novice
30 0 2 novice
"""

_golden_rows = [line.split() for line in _GOLDEN_TABLE.splitlines() if line.strip()]

# Depth: longest path from a source row in the derived ladder (sources are 0).
GOLDEN_DEPTH: dict[str, int] = {row[0]: int(row[1]) for row in _golden_rows}

# Element interactivity: decisions (autonomy map) + concepts touched.
GOLDEN_EI: dict[str, int] = {row[0]: int(row[2]) for row in _golden_rows}

# The computed rung per row under the monotone threshold table.
GOLDEN_COMPUTED: dict[str, str] = {row[0]: row[3] for row in _golden_rows}

# The stored rungs that disagree with the computation; the pin wins.
PINNED_IDS = {"03", "06", "08", "09", "12", "18"}


def _row(
    mid: str,
    *,
    produces: tuple[str, ...] = (),
    consumes: tuple[str, ...] = (),
    after: tuple[str, ...] = (),
    autonomy: str = "automated_with_audit",
    concepts: tuple[str, ...] = ("keyword_rules",),
    difficulty: str | None = None,
) -> MethodRecord:
    """One minimal valid register row (the issue-439 helper, plus axes)."""
    return MethodRecord(
        id=mid,
        name=f"M{mid}",
        summary="A short active summary for the test row.",
        paradigm="unsupervised",
        evidence_label="model_based",
        inputs_required=["raw_text"],
        calibration_effort="low",
        autonomy=autonomy,
        complexity_tier="baseline",
        selection_priority="first",
        selection_notes="First for the test need.",
        robustness_caveat="Results change with the input wording.",
        bias_risk="The method can favor common wording.",
        efficiency_workload="One pass over n records.",
        output_family="themes",
        output_shape="A ranked list with scores.",
        usage_guidance="Use it for first inspection.",
        validation_required="Inspect a sample before use.",
        permitted_claims="Exploratory wording claims.",
        do_not_claim="Do not claim validated themes.",
        implementation_status="implemented_new",
        op="topics_build",
        sources=["https://example.org/source"],
        difficulty=difficulty,
        concepts=list(concepts) or None,
        produces=list(produces) or None,
        consumes=list(consumes) or None,
        after=list(after) or None,
    )


def _record(mid: str, *, autonomy: str, concepts: list[str]) -> MethodRecord:
    """One row carrying only the interactivity axes."""
    return _row(mid, autonomy=autonomy, concepts=tuple(concepts))


def _chain(*ids: str) -> list[MethodRecord]:
    """Rows in teaching order: each row but the first follows its predecessor."""
    return [_row(mid, after=(ids[i - 1],) if i else ()) for i, mid in enumerate(ids)]


def test_depth_matches_the_golden_ladder() -> None:
    register = load_method_register({})
    assert graph_depth(register.methods) == GOLDEN_DEPTH


def test_element_interactivity_matches_golden() -> None:
    register = load_method_register({})
    by_id = {row.id: row for row in register.methods}
    assert {mid: element_interactivity(by_id[mid]) for mid in by_id} == GOLDEN_EI


def test_agreement_is_exactly_24_rows_with_six_pins() -> None:
    scores = difficulty_scores(load_method_register({}))
    assert [s.method_id for s in scores if s.matches] == [
        mid for mid in ALL_IDS if mid not in PINNED_IDS
    ]
    assert [s.method_id for s in scores if not s.matches] == ["03", "06", "08", "09", "12", "18"]
    assert [s.computed_rung for s in scores if s.matches] == [
        GOLDEN_COMPUTED[s.method_id] for s in scores if s.matches
    ]


def test_graph_depth_is_longest_path_not_edge_count() -> None:
    """Chain 01->02->03->04 via after edges: the depth of 04 is 3, not 1."""
    rows = _chain("01", "02", "03", "04")
    assert graph_depth(rows) == {"01": 0, "02": 1, "03": 2, "04": 3}


def test_element_interactivity_reads_only_existing_axes() -> None:
    """EI reads autonomy and concepts only; no new authored axis exists."""
    record = _record(
        "09",
        autonomy="automated_with_audit",
        concepts=["topic_modeling", "clustering", "vector_similarity"],
    )
    assert element_interactivity(record) == 3
    assert "recipe" not in record.model_dump()


def test_decision_weights_map_pins_the_autonomy_vocabulary() -> None:
    assert DECISION_WEIGHTS == {"human_led": 2, "human_in_loop": 1, "automated_with_audit": 0}


def test_mismatches_are_soft_and_never_mutate() -> None:
    """The stored pin stays authoritative; scoring writes nothing back."""
    register = load_method_register({})
    scores = {score.method_id: score for score in difficulty_scores(register)}
    for row in register.methods:
        assert row.difficulty is not None
        score = scores[row.id]
        assert score.matches is (row.difficulty == score.computed_rung)
    assert all("computed_rung" not in row.model_dump() for row in register.methods)


def test_mismatch_report_raises_no_warnings(monkeypatch) -> None:
    """The report is plain strings; it never calls warnings.warn."""
    recorded: list[tuple[object, ...]] = []

    def _record(*args: object, **kwargs: object) -> None:
        recorded.append(args)

    monkeypatch.setattr(warnings, "warn", _record)
    lines = difficulty_mismatches(load_method_register({}))
    assert recorded == [] and len(lines) == 6 and lines == sorted(lines)
    assert lines[0] == "Method 03 computes intermediate; the register pins advanced."


def test_threshold_table_is_monotone() -> None:
    """Every (depth, ei) band returns a rung; deep rows land on advanced."""
    for depth in range(6):
        for ei in range(1, 5):
            assert computed_rung(depth, ei) in DIFFICULTY_LEVELS
    assert computed_rung(4, 1) == "advanced" and computed_rung(5, 3) == "advanced"


def test_difficulty_score_snapshot(snapshot: Any) -> None:
    """All 30 rows' scores in one pinned snapshot (deterministic, id-sorted)."""
    scores = difficulty_scores(load_method_register({}))
    payload = [
        {
            "id": score.method_id,
            "depth": score.graph_depth,
            "ei": score.element_interactivity,
            "computed": score.computed_rung,
            "stored": score.stored_rung,
            "matches": score.matches,
        }
        for score in scores
    ]
    assert payload == snapshot
