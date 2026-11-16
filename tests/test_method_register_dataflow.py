"""Golden v1 ladder fixture and the derived delta map for the dataflow ladder.

The freeze constants pin the hand-authored v1 links (register 3.2.0,
issue 431). The derived tables are the reviewed acceptance columns from
the derived-ladder plan (issue 439). Derivation, journey, and route
tests import these constants.
"""

from __future__ import annotations

import pytest

from corpus_kb.analysis.method_register import MethodRecord
from corpus_kb.analysis.method_register_dataflow import (
    artifact_prerequisites,
    derive_edges,
    ladder_next,
    ladder_prerequisites,
    validate_dataflow_graph,
)
from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.tools.analysis_route_next import next_methods_for

ALL_IDS = [f"{index:02d}" for index in range(1, 31)]

# v1 hand-authored links (register 3.2.0), frozen from master.
GOLDEN_PREREQS: dict[str, tuple[str, ...]] = dict.fromkeys(ALL_IDS, ())
GOLDEN_PREREQS.update(
    {
        "13": ("04",),
        "14": ("04",),
        "15": ("04",),
        "20": ("16",),
    }
)

GOLDEN_NEXT: dict[str, tuple[str, ...]] = dict.fromkeys(ALL_IDS, ())
GOLDEN_NEXT.update(
    {
        "02": ("03",),
        "04": ("13", "14", "15"),
        "07": ("06",),
        "09": ("10", "11"),
        "12": ("04",),
        "13": ("14",),
        "15": ("16", "17"),
        "16": ("17", "20"),
        "18": ("04",),
        "19": ("16",),
        "23": ("28",),
    }
)

# Reviewed derived ladder: the acceptance columns of the plan's delta table.
EXPECTED_DERIVED_PREREQS: dict[str, tuple[str, ...]] = dict.fromkeys(ALL_IDS, ())
EXPECTED_DERIVED_PREREQS.update(
    {
        "03": ("02",),
        "04": ("12", "18"),
        "06": ("07",),
        "10": ("09",),
        "11": ("04", "09"),
        "13": ("04",),
        "14": ("04", "13"),
        "15": ("04",),
        "16": ("04", "15", "19"),
        "17": ("04", "15", "16"),
        "20": ("16", "17"),
        "27": ("04",),
        "28": ("23",),
    }
)

EXPECTED_DERIVED_NEXT: dict[str, tuple[str, ...]] = dict.fromkeys(ALL_IDS, ())
EXPECTED_DERIVED_NEXT.update(
    {
        "02": ("03",),
        "04": ("11", "13", "14", "15", "16", "17", "27"),
        "07": ("06",),
        "09": ("10", "11"),
        "12": ("04",),
        "13": ("14",),
        "15": ("16", "17"),
        "16": ("17", "20"),
        "17": ("20",),
        "18": ("04",),
        "19": ("16",),
        "23": ("28",),
    }
)


def test_golden_tables_cover_all_30_ids() -> None:
    for table in (GOLDEN_PREREQS, GOLDEN_NEXT, EXPECTED_DERIVED_PREREQS, EXPECTED_DERIVED_NEXT):
        assert list(table) == ALL_IDS


def test_golden_tables_match_the_live_v1_register() -> None:
    """The freeze captures v1, so this passes on master before any edit."""
    register = load_method_register({})
    by_id = {row.id: row for row in register.methods}
    for mid, row in by_id.items():
        assert list(row.prerequisites or []) == list(GOLDEN_PREREQS[mid])
        assert list(row.next or []) == list(GOLDEN_NEXT[mid])


# ---------------------------------------------------------------------------
# Derivation engine (consumes ∩ produces, merged with after) — issue 439
# ---------------------------------------------------------------------------


def _row(
    mid: str,
    *,
    produces: tuple[str, ...] = (),
    consumes: tuple[str, ...] = (),
    after: tuple[str, ...] = (),
) -> MethodRecord:
    """One minimal valid register row carrying the three dataflow lists."""
    return MethodRecord(
        id=mid,
        name=f"M{mid}",
        summary="A short active summary for the test row.",
        paradigm="unsupervised",
        evidence_label="model_based",
        inputs_required=["raw_text"],
        calibration_effort="low",
        autonomy="automated_with_audit",
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
        produces=list(produces) or None,
        consumes=list(consumes) or None,
        after=list(after) or None,
    )


def test_edges_are_consumes_cap_produces() -> None:
    rows = [_row("04", produces=["labels"]), _row("13", consumes=["labels"])]
    assert derive_edges(rows) == [("04", "13")]


def test_any_of_producers_union_direct_id_sorted() -> None:
    rows = [
        _row("04", produces=["labels"]),
        _row("19", produces=["labels"]),
        _row("13", consumes=["labels"]),
        _row("20", consumes=["predictions"], after=["16"]),
    ]
    assert artifact_prerequisites(rows, "13") == ["04", "19"]


def test_after_merges_with_dataflow_edges() -> None:
    rows = [
        _row("04", produces=["labels"]),
        _row("13", consumes=["labels"]),
        _row("14", consumes=["labels"], after=["13"]),
    ]
    assert ladder_prerequisites(rows, "14") == ["04", "13"]


def test_ladder_next_is_the_id_sorted_reverse() -> None:
    rows = [
        _row("04", produces=["labels"]),
        _row("13", consumes=["labels"]),
        _row("15", consumes=["labels"]),
    ]
    assert ladder_next(rows, "04") == ["13", "15"]


def test_empty_overlap_isolates() -> None:
    rows = [_row("05", produces=["keyphrases"]), _row("23", produces=["evidence"], consumes=[])]
    assert ladder_prerequisites(rows, "23") == []
    assert ladder_next(rows, "05") == []


def test_artifact_cycle_rejected() -> None:
    rows = [
        _row("16", produces=["predictions"], consumes=["labels"]),
        _row("19", produces=["labels"], consumes=["predictions"]),
    ]
    with pytest.raises(ValueError, match="cycle"):
        validate_dataflow_graph(rows)


def test_after_cycle_rejected() -> None:
    rows = [_row("02", after=["03"]), _row("03", after=["02"])]
    with pytest.raises(ValueError, match="cycle"):
        validate_dataflow_graph(rows)


def test_after_target_must_exist() -> None:
    with pytest.raises(ValueError, match="99"):
        validate_dataflow_graph([_row("02", after=["99"])])


def test_corpus_supplied_inputs_yield_no_edge() -> None:
    rows = [_row("09", produces=["topics"]), _row("10", consumes=["topics"])]
    rows.append(_row("24", consumes=[]))
    assert artifact_prerequisites(rows, "24") == []
    assert derive_edges(rows) == [("09", "10")]


def test_route_next_methods_matches_v1_on_golden_fixture() -> None:
    """Live route output: unchanged everywhere except rows 04 and 17.

    Green from the consumer swap (plan Task 5); until then
    ``next_methods_for`` still reads the hand-authored v1 chains.
    """
    register = load_method_register({})
    expected = {"04": ["11", "13", "14"], "17": ["20"]}
    for row in register.methods:
        derived = [item["method_id"] for item in next_methods_for(register, row)]
        if row.id in expected:
            assert derived == expected[row.id], row.id
        else:
            assert derived == list(GOLDEN_NEXT[row.id]), row.id


def test_derived_ladder_equals_v1_except_the_reviewed_delta() -> None:
    """The authored register derives the reviewed acceptance columns."""
    register = load_method_register({})
    methods = register.methods
    for row in methods:
        assert ladder_prerequisites(methods, row.id) == list(EXPECTED_DERIVED_PREREQS[row.id]), (
            row.id
        )
        assert ladder_next(methods, row.id) == list(EXPECTED_DERIVED_NEXT[row.id]), row.id


def test_every_v1_edge_is_expressed() -> None:
    """Every hand-authored v1 edge survives as a derived ladder edge."""
    register = load_method_register({})
    methods = register.methods
    for row in methods:
        for target in row.next or []:
            assert row.id in ladder_prerequisites(methods, target), (row.id, target)
        for prereq in row.prerequisites or []:
            assert row.id in ladder_next(methods, prereq), (row.id, prereq)
