"""Golden agreement table for the difficulty seed (issue 440).

The table pins each register row's graph depth, element interactivity,
and computed rung, plus the six stored pins that win. Frozen from the
3.3.0 register on master; the plan's Feasibility section is the record.
"""

from __future__ import annotations

from corpus_kb.analysis.method_register import MethodRecord
from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.analysis.method_register_difficulty import (
    difficulty_scores,
    element_interactivity,
    graph_depth,
)

ALL_IDS = [f"{index:02d}" for index in range(1, 31)]

# Depth: longest path from a source row in the derived ladder (sources are 0).
GOLDEN_DEPTH: dict[str, int] = {
    "01": 0, "02": 0, "03": 1, "04": 1, "05": 0, "06": 1,
    "07": 0, "08": 0, "09": 0, "10": 1, "11": 2, "12": 0,
    "13": 2, "14": 3, "15": 2, "16": 3, "17": 4, "18": 0,
    "19": 0, "20": 5, "21": 0, "22": 0, "23": 0, "24": 0,
    "25": 0, "26": 0, "27": 2, "28": 1, "29": 0, "30": 0,
}

# Element interactivity: decisions (autonomy map) + concepts touched.
GOLDEN_EI: dict[str, int] = {
    "01": 3, "02": 2, "03": 2, "04": 1, "05": 1, "06": 1,
    "07": 1, "08": 2, "09": 3, "10": 4, "11": 4, "12": 3,
    "13": 2, "14": 2, "15": 2, "16": 2, "17": 2, "18": 1,
    "19": 3, "20": 3, "21": 3, "22": 3, "23": 2, "24": 1,
    "25": 1, "26": 2, "27": 3, "28": 3, "29": 1, "30": 2,
}

# The computed rung per row under the monotone threshold table.
GOLDEN_COMPUTED: dict[str, str] = {
    "01": "advanced", "02": "novice", "03": "intermediate", "04": "novice",
    "05": "novice", "06": "novice", "07": "novice", "08": "novice",
    "09": "advanced", "10": "intermediate", "11": "intermediate",
    "12": "advanced", "13": "intermediate", "14": "intermediate",
    "15": "intermediate", "16": "intermediate", "17": "advanced",
    "18": "novice", "19": "advanced", "20": "advanced", "21": "advanced",
    "22": "advanced", "23": "novice", "24": "novice", "25": "novice",
    "26": "novice", "27": "intermediate", "28": "intermediate",
    "29": "novice", "30": "novice",
}

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
    """Rows in teaching order: every row but the last follows the next one."""
    return [_row(mid, after=(ids[i + 1],)) for i, mid in enumerate(ids[:-1])] + [
        _row(ids[-1])
    ]


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
    assert [s.method_id for s in scores if not s.matches] == [
        "03", "06", "08", "09", "12", "18"
    ]
    assert [s.computed_rung for s in scores if s.matches] == [
        GOLDEN_COMPUTED[s.method_id] for s in scores if s.matches
    ]
