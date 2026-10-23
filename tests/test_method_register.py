"""Tests for the analysis method register (AMR program, wave 1, agent A).

Covers: schema conformance, controlled vocabularies with remediation text,
the sources floor, the exactly-30-ids ledger, status/op consistency, the
byte-identical packaged mirror, and the deterministic selector rubric with
golden snapshots.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from corpus_kb.analysis.method_register import (
    MethodRegister,
    load_method_register,
)
from corpus_kb.analysis.method_register_rubric import rank_methods

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_YAML = REPO_ROOT / "config" / "analysis_methods.yaml"
PACKAGED_YAML = REPO_ROOT / "src" / "corpus_kb" / "config" / "analysis_methods.yaml"

HUMAN_LED_IDS = {"01", "21"}
NOT_PLANNED_IDS = {"08"}
NEW_OP_IDS = {
    "05",
    "06",
    "07",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "18",
    "20",
    "22",
    "26",
    "27",
    "28",
    "29",
    "30",
}
EXISTING_OP_IDS = {
    "02",
    "03",
    "04",
    "09",
    "16",
    "17",
    "19",
    "23",
    "24",
    "25",
}

EXPECTED_EXISTING_OPS = {
    "02": "analyze_run",
    "03": "analyze_run",
    "04": "labeling_build",
    "09": "topics_build",
    "16": "classification_build",
    "17": "classification_build",
    "19": "labeling_build",
    "23": "search",
    "24": "ask",
    "25": "ingest_text",
}

EXPECTED_NEW_OPS = {
    "05": "keyphrases_extract",
    "06": "keyphrases_extract",
    "07": "keyphrases_extract",
    "10": "topics_build",
    "11": "topics_build",
    "12": "clusters_inspect",
    "13": "centroids_classify",
    "14": "neighbors_classify",
    "15": "lexical_classify",
    "18": "nli_classify",
    "20": "active_learn_next",
    "22": "analyze_run",
    "26": "sentiment_screen",
    "27": "sentiment_screen",
    "28": "contradictions_screen",
    "29": "duplicates_screen",
    "30": "outliers_rank",
}


def _valid_record() -> dict[str, Any]:
    """One fully valid method record to mutate in rejection tests."""
    return {
        "id": "01",
        "name": "Test method",
        "summary": "A short active summary for the test record.",
        "paradigm": "unsupervised",
        "evidence_label": "model_based",
        "inputs_required": ["raw_text"],
        "calibration_effort": "low",
        "autonomy": "automated_with_audit",
        "complexity_tier": "baseline",
        "selection_priority": "first",
        "selection_notes": "First for the test need.",
        "robustness_caveat": "Results change with the input wording.",
        "bias_risk": "The method can favor common wording.",
        "efficiency_workload": "One pass over n records.",
        "output_family": "themes",
        "output_shape": "A ranked list with scores.",
        "usage_guidance": "Use it for first inspection.",
        "validation_required": "Inspect a sample before use.",
        "permitted_claims": "Exploratory wording claims.",
        "do_not_claim": "Do not claim validated themes.",
        "implementation_status": "implemented_new",
        "op": "topics_build",
        "sources": ["https://example.org/source"],
    }


@pytest.fixture()
def register() -> MethodRegister:
    return load_method_register({})


# ---------------------------------------------------------------------------
# Loading and header
# ---------------------------------------------------------------------------


def test_loads_with_defaults_and_header(register: MethodRegister) -> None:
    assert register.register_version == "1.0.0"
    assert register.source_document.title
    assert register.source_document.path_note
    assert len(register.selection_rules) == 4
    assert register.adjacent_candidates
    assert "Top2Vec" in register.adjacent_candidates
    assert "KeyLLM" in register.adjacent_candidates
    assert "MMR" in register.adjacent_candidates


def test_exactly_ids_01_to_30_each_once(register: MethodRegister) -> None:
    ids = [record.id for record in register.methods]
    assert sorted(ids) == [f"{i:02d}" for i in range(1, 31)]
    assert len(set(ids)) == 30


def test_descriptive_axes_present_on_every_row(register: MethodRegister) -> None:
    for record in register.methods:
        assert record.summary
        assert record.selection_notes
        assert record.robustness_caveat
        assert record.bias_risk
        assert record.efficiency_workload
        assert record.output_shape
        assert record.usage_guidance
        assert record.validation_required
        assert record.permitted_claims
        assert record.do_not_claim
        assert record.sources
        assert all(source.startswith(("http://", "https://")) for source in record.sources)


def test_packaged_mirror_is_byte_identical() -> None:
    root_hash = hashlib.sha256(ROOT_YAML.read_bytes()).hexdigest()
    packaged_hash = hashlib.sha256(PACKAGED_YAML.read_bytes()).hexdigest()
    assert root_hash == packaged_hash


def test_packaged_fallback_when_repo_file_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    register = load_method_register({})
    assert register.register_version == "1.0.0"
    assert len(register.methods) == 30


def test_missing_register_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "corpus_kb.analysis.method_register._packaged_register_path",
        lambda _name: None,
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        load_method_register({})


# ---------------------------------------------------------------------------
# Controlled vocabularies with remediation text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "bad", "allowed"),
    [
        ("paradigm", "quant", "unsupervised"),
        ("evidence_label", "vibes", "model_based"),
        ("calibration_effort", "medium", "moderate"),
        ("autonomy", "robot", "human_in_loop"),
        ("complexity_tier", "hard", "standard"),
        ("selection_priority", "maybe", "avoid_first"),
        ("output_family", "widgets", "errors"),
        ("implementation_status", "someday", "implemented_new"),
    ],
)
def test_vocab_rejection_names_allowed_values(field: str, bad: str, allowed: str) -> None:
    record = _valid_record()
    record[field] = bad
    with pytest.raises(ValueError) as excinfo:
        MethodRegister.model_validate(_single_register(record))
    message = str(excinfo.value)
    assert field in message
    assert allowed in message


def _single_register(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "register_version": "1.0.0",
        "source_document": {"title": "t", "path_note": "p"},
        "selection_rules": ["rule"],
        "adjacent_candidates": "note",
        "methods": [record],
    }


def test_bad_input_kind_rejected() -> None:
    record = _valid_record()
    record["inputs_required"] = ["vectors"]
    with pytest.raises(ValueError, match="inputs_required"):
        MethodRegister.model_validate(_single_register(record))


def test_id_must_be_zero_padded_in_range() -> None:
    record = _valid_record()
    record["id"] = "1"
    with pytest.raises(ValueError, match="zero-padded"):
        MethodRegister.model_validate(_single_register(record))
    record["id"] = "31"
    with pytest.raises(ValueError, match="31"):
        MethodRegister.model_validate(_single_register(record))


def test_summary_word_cap() -> None:
    record = _valid_record()
    record["summary"] = " ".join(["word"] * 41)
    with pytest.raises(ValueError, match="40 words"):
        MethodRegister.model_validate(_single_register(record))


def test_sources_floor() -> None:
    record = _valid_record()
    record["sources"] = []
    with pytest.raises(ValueError, match="URL"):
        MethodRegister.model_validate(_single_register(record))
    record["sources"] = ["not-a-url"]
    with pytest.raises(ValueError, match="URL"):
        MethodRegister.model_validate(_single_register(record))


def test_unknown_key_rejected() -> None:
    record = _valid_record()
    record["predicted_accuracy"] = "95%"
    with pytest.raises(ValueError, match="predicted_accuracy"):
        MethodRegister.model_validate(_single_register(record))


# ---------------------------------------------------------------------------
# Status/op consistency and the coverage ledger
# ---------------------------------------------------------------------------


def test_op_required_for_implemented_status() -> None:
    record = _valid_record()
    record["implementation_status"] = "implemented_existing"
    record["op"] = None
    with pytest.raises(ValueError, match="op"):
        MethodRegister.model_validate(_single_register(record))


def test_op_must_be_null_for_non_implemented_status() -> None:
    record = _valid_record()
    record["implementation_status"] = "human_led"
    record["op"] = "topics_build"
    with pytest.raises(ValueError, match="null"):
        MethodRegister.model_validate(_single_register(record))


def test_ledger_partition(register: MethodRegister) -> None:
    human_led = {r.id for r in register.methods if r.implementation_status == "human_led"}
    not_planned = {r.id for r in register.methods if r.implementation_status == "not_planned"}
    new_ops = {r.id for r in register.methods if r.implementation_status == "implemented_new"}
    existing = {r.id for r in register.methods if r.implementation_status == "implemented_existing"}
    assert human_led == HUMAN_LED_IDS
    assert not_planned == NOT_PLANNED_IDS
    assert new_ops == NEW_OP_IDS
    assert existing == EXISTING_OP_IDS


def test_existing_ops_point_at_catalogue(register: MethodRegister) -> None:
    from corpus_kb.contracts import OPERATIONS

    names = {operation.name for operation in OPERATIONS}
    for record in register.methods:
        if record.implementation_status == "implemented_existing":
            assert record.op in names, f"{record.id} points at unknown op {record.op}"
            assert EXPECTED_EXISTING_OPS[record.id] == record.op


def test_new_ops_use_planned_names(register: MethodRegister) -> None:
    for record in register.methods:
        if record.implementation_status == "implemented_new":
            assert EXPECTED_NEW_OPS[record.id] == record.op


# ---------------------------------------------------------------------------
# Selector rubric: ordering, tiebreaks, determinism
# ---------------------------------------------------------------------------


def _mini_register(*records: dict[str, Any]) -> MethodRegister:
    return MethodRegister.model_validate(
        {
            "register_version": "1.0.0",
            "source_document": {"title": "t", "path_note": "p"},
            "selection_rules": ["rule"],
            "adjacent_candidates": "note",
            "methods": list(records),
        }
    )


def _mutate(base: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    record = dict(base)
    record.update(overrides)
    return record


def test_family_match_ranks_first() -> None:
    matching = _mutate(_valid_record(), id="02", output_family="labels")
    other = _mutate(_valid_record(), id="03", output_family="themes")
    ranked = rank_methods(_mini_register(matching, other), output_family="labels")
    assert [r.id for r in ranked] == ["02", "03"]
    assert "family" in ranked[0].rank_reason


def test_missing_inputs_rank_lower_and_are_sorted() -> None:
    covered = _mutate(_valid_record(), id="02", inputs_required=["raw_text"])
    missing = _mutate(_valid_record(), id="03", inputs_required=["embeddings", "llm"])
    ranked = rank_methods(_mini_register(covered, missing), inputs_available=["raw_text"])
    assert [r.id for r in ranked] == ["02", "03"]
    assert ranked[1].missing_inputs == ["embeddings", "llm"]


def test_complexity_tier_ascending_tiebreak() -> None:
    baseline = _mutate(_valid_record(), id="02", complexity_tier="baseline")
    advanced = _mutate(_valid_record(), id="03", complexity_tier="advanced")
    ranked = rank_methods(_mini_register(advanced, baseline))
    assert [r.id for r in ranked] == ["02", "03"]
    assert "tier" in ranked[1].rank_reason


def test_calibration_effort_tiebreak() -> None:
    low = _mutate(_valid_record(), id="02", calibration_effort="low")
    high = _mutate(_valid_record(), id="03", calibration_effort="high")
    ranked = rank_methods(_mini_register(high, low))
    assert [r.id for r in ranked] == ["02", "03"]
    assert "calibration" in ranked[1].rank_reason.lower()


def test_selection_priority_tiebreak() -> None:
    first = _mutate(_valid_record(), id="02", selection_priority="first")
    avoid = _mutate(_valid_record(), id="03", selection_priority="avoid_first")
    ranked = rank_methods(_mini_register(avoid, first))
    assert [r.id for r in ranked] == ["02", "03"]
    assert "priority" in ranked[1].rank_reason


def test_id_ascending_final_tiebreak() -> None:
    early = _mutate(_valid_record(), id="02")
    late = _mutate(_valid_record(), id="03")
    ranked = rank_methods(_mini_register(late, early))
    assert [r.id for r in ranked] == ["02", "03"]
    assert "id" in ranked[1].rank_reason


def test_complexity_cap_filters() -> None:
    baseline = _mutate(_valid_record(), id="02", complexity_tier="baseline")
    standard = _mutate(_valid_record(), id="03", complexity_tier="standard")
    ranked = rank_methods(_mini_register(baseline, standard), max_complexity_tier="baseline")
    assert [r.id for r in ranked] == ["02"]


def test_rank_methods_is_deterministic(register: MethodRegister) -> None:
    first = rank_methods(
        register,
        output_family="labels",
        inputs_available=["raw_text", "embeddings", "labels"],
    )
    second = rank_methods(
        register,
        output_family="labels",
        inputs_available=["raw_text", "embeddings", "labels"],
    )
    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]


def test_invalid_axis_arguments_raise(register: MethodRegister) -> None:
    with pytest.raises(ValueError, match="output_family"):
        rank_methods(register, output_family="widgets")
    with pytest.raises(ValueError, match="complexity_tier"):
        rank_methods(register, max_complexity_tier="hard")


def test_recommendation_carries_descriptive_axes(register: MethodRegister) -> None:
    ranked = rank_methods(register, output_family="themes", inputs_available=["raw_text"])
    top = ranked[0].model_dump()
    for key in (
        "id",
        "name",
        "rank",
        "rank_reason",
        "missing_inputs",
        "summary",
        "output_shape",
        "usage_guidance",
        "validation_required",
        "permitted_claims",
        "do_not_claim",
        "robustness_caveat",
        "bias_risk",
        "efficiency_workload",
        "sources",
        "implementation_status",
        "op",
    ):
        assert key in top, f"recommendation missing {key}"


# ---------------------------------------------------------------------------
# Golden snapshots of the real register
# ---------------------------------------------------------------------------


def test_golden_labels_ranking(register: MethodRegister) -> None:
    ranked = rank_methods(
        register,
        output_family="labels",
        inputs_available=["raw_text", "embeddings", "labels"],
    )
    # The labels block leads in exactly this order; other families follow.
    assert [r.id for r in ranked[:9]] == [
        "04",
        "13",
        "14",
        "15",
        "16",
        "27",
        "17",
        "18",
        "19",
    ]
    assert all(r.output_family == "labels" for r in ranked[:9])
    assert all(r.output_family != "labels" for r in ranked[9:])
    assert ranked[0].missing_inputs == []
    assert ranked[7].missing_inputs == ["llm"]
    assert ranked[8].missing_inputs == ["predictions"]


def test_golden_themes_ranking(register: MethodRegister) -> None:
    ranked = rank_methods(
        register,
        output_family="themes",
        inputs_available=["raw_text", "embeddings", "seed_labels", "labels", "llm"],
    )
    assert [r.id for r in ranked[:9]] == [
        "05",
        "07",
        "06",
        "09",
        "10",
        "11",
        "08",
        "01",
        "21",
    ]
    assert all(r.output_family == "themes" for r in ranked[:9])
    assert all(r.missing_inputs == [] for r in ranked[:9])
    dump = cast(dict[str, Any], ranked[0].model_dump())
    assert dump["rank"] == 1


def test_loader_enforces_full_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = []
    for index in range(1, 31):
        if index == 15:
            continue
        rows.append(_mutate(_valid_record(), id=f"{index:02d}", name=f"M{index:02d}"))
    payload = {
        "register_version": "1.0.0",
        "source_document": {"title": "t", "path_note": "p"},
        "selection_rules": ["rule"],
        "adjacent_candidates": "note",
        "methods": rows,
    }
    path = tmp_path / "register.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    config = {"analysis": {"method_register_path": str(path)}}
    with pytest.raises(ValueError, match="01 to 30"):
        load_method_register(config)
