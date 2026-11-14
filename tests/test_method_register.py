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

from corpus_kb.analysis.method_register import MethodRegister
from corpus_kb.analysis.method_register_loader import load_method_register
from corpus_kb.analysis.method_register_progression import (
    DIFFICULTY_LEVELS,
    TEACHING_CONCEPTS,
)
from corpus_kb.analysis.method_register_routing import (
    COMPARISON_STRUCTURES,
    COST_TIERS,
    EVIDENCE_BASES,
    OUTPUT_FORMS,
    STATISTICAL_RIGOR_LEVELS,
    TASK_TYPES,
)
from corpus_kb.analysis.method_register_rubric import rank_methods
from corpus_kb.analysis.method_register_tables import COLUMN_TYPES
from corpus_kb.api._body import error_response
from corpus_kb.errors import ValidationError

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
    assert register.register_version == "3.2.0"
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
    assert register.register_version == "3.2.0"
    assert len(register.methods) == 30


def test_missing_register_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "corpus_kb.analysis.method_register_loader._packaged_register_path",
        lambda _name: None,
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        load_method_register({})


def test_register_not_a_mapping_keeps_the_400_wire_contract(tmp_path: Path) -> None:
    """A non-mapping register file answers 400 and CLI exit 1 (issue 229)."""
    path = tmp_path / "register.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    config = {"analysis": {"method_register_path": str(path)}}
    with pytest.raises(ValidationError, match="must be a YAML mapping") as excinfo:
        load_method_register(config)
    exc = excinfo.value
    assert (exc.http_status, exc.socket_code, exc.cli_exit) == (400, -32602, 1)
    assert error_response(exc).status_code == 400


def test_schema_mismatch_names_the_source(tmp_path: Path) -> None:
    """A schema-mismatched register names the file and stays on the 400 wire."""
    path = tmp_path / "register.yaml"
    payload = {
        "register_version": "1.0.0",
        "source_document": {"title": "t", "path_note": "p"},
        "selection_rules": ["rule"],
        "adjacent_candidates": "note",
        "methods": [{"id": "01"}],
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    config = {"analysis": {"method_register_path": str(path)}}
    with pytest.raises(ValidationError, match="Malformed analysis method register") as excinfo:
        load_method_register(config)
    exc = excinfo.value
    assert "register.yaml" in str(exc)
    assert (exc.http_status, exc.socket_code, exc.cli_exit) == (400, -32602, 1)
    assert error_response(exc).status_code == 400


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
    with pytest.raises(ValidationError, match="01 to 30"):
        load_method_register(config)


# ---------------------------------------------------------------------------
# Routing axes (analysis-routing program, wave 1)
# ---------------------------------------------------------------------------

ROUTING_FIELD_NAMES = (
    "task_type",
    "comparison_structure",
    "output_form",
    "evidence_basis",
    "statistical_rigor",
    "cost_tier",
    "question_phrases",
    "failure_modes",
    "scope_limits",
    "suggests",
)


def test_routing_vocab_tuples_are_pinned() -> None:
    """The six routing vocabularies match the program plan verbatim."""
    assert TASK_TYPES == (
        "representation",
        "discovery",
        "measurement",
        "prediction",
        "causal_inference",
    )
    assert COMPARISON_STRUCTURES == (
        "none",
        "subset_vs_subset",
        "paired",
        "temporal",
        "cross_corpus",
        "model_vs_model",
    )
    assert OUTPUT_FORMS == (
        "point_answer",
        "ranked_list",
        "statistic",
        "cluster_map",
        "graph",
        "narrative",
    )
    assert EVIDENCE_BASES == ("vector", "lexical", "claims", "graph", "labels")
    assert STATISTICAL_RIGOR_LEVELS == (
        "descriptive",
        "inferential_null",
        "inferential_calibrated",
    )
    assert COST_TIERS == ("interactive", "batch")


def _routing_record() -> dict[str, Any]:
    """One valid record carrying all 10 routing fields."""
    record = _valid_record()
    record.update(
        {
            "task_type": "discovery",
            "comparison_structure": "none",
            "output_form": "ranked_list",
            "evidence_basis": "lexical",
            "statistical_rigor": "descriptive",
            "cost_tier": "interactive",
            "question_phrases": ["What words come up most often in these documents?"],
            "failure_modes": "Repeated boilerplate can outrank meaningful terms.",
            "scope_limits": "Distinctive wording only; themes need reading.",
            "suggests": ["Which top terms look meaningless here?", "What rare words rank high?"],
        }
    )
    return record


@pytest.mark.parametrize("field", ROUTING_FIELD_NAMES)
def test_routing_fields_default_to_none(field: str) -> None:
    register = MethodRegister.model_validate(_single_register(_valid_record()))
    assert getattr(register.methods[0], field) is None


@pytest.mark.parametrize(
    ("field", "bad", "allowed"),
    [
        ("task_type", "causal", "causal_inference"),
        ("comparison_structure", "versus", "paired"),
        ("output_form", "table", "cluster_map"),
        ("evidence_basis", "vibes", "vector"),
        ("statistical_rigor", "strong", "descriptive"),
        ("cost_tier", "cheap", "interactive"),
    ],
)
def test_routing_vocab_rejection_names_allowed_values(field: str, bad: str, allowed: str) -> None:
    record = _routing_record()
    record[field] = bad
    with pytest.raises(ValueError) as excinfo:
        MethodRegister.model_validate(_single_register(record))
    message = str(excinfo.value)
    assert field in message
    assert allowed in message


def test_question_phrases_reject_bad_shapes() -> None:
    base = _routing_record()
    record = _mutate(base, question_phrases=[])
    with pytest.raises(ValueError, match="question_phrases"):
        MethodRegister.model_validate(_single_register(record))
    record = _mutate(base, question_phrases=[f"distinct phrase number {n}" for n in range(11)])
    with pytest.raises(ValueError, match="1 to 10"):
        MethodRegister.model_validate(_single_register(record))
    record = _mutate(base, question_phrases=["   "])
    with pytest.raises(ValueError, match="non-empty"):
        MethodRegister.model_validate(_single_register(record))
    record = _mutate(
        base,
        question_phrases=["same words repeated here", "same words repeated here"],
    )
    with pytest.raises(ValueError, match="duplicate"):
        MethodRegister.model_validate(_single_register(record))


def test_suggests_reject_count_and_word_cap() -> None:
    base = _routing_record()
    record = _mutate(base, suggests=["one", "two", "three", "four"])
    with pytest.raises(ValueError, match="1 to 3"):
        MethodRegister.model_validate(_single_register(record))
    record = _mutate(base, suggests=[" ".join(["word"] * 21)])
    with pytest.raises(ValueError, match="20 words"):
        MethodRegister.model_validate(_single_register(record))
    record = _mutate(base, suggests=["   "])
    with pytest.raises(ValueError, match="non-empty"):
        MethodRegister.model_validate(_single_register(record))


def test_suggests_reject_duplicates() -> None:
    """Two identical chip strings collide in the UI; the schema rejects them."""
    base = _routing_record()
    record = _mutate(base, suggests=["Ask the same thing twice?", "Ask the same thing twice?"])
    with pytest.raises(ValueError, match="duplicate"):
        MethodRegister.model_validate(_single_register(record))


def test_dropped_routing_fields_rejected() -> None:
    """cost_scale and evidence_citation left the schema at 2.0.0, and
    assumptions and null_calibration left it at 3.0.0 (extra=forbid)."""
    record = _mutate(_routing_record(), cost_scale="One sparse scan over the corpus; CPU only.")
    with pytest.raises(ValueError, match="cost_scale"):
        MethodRegister.model_validate(_single_register(record))
    record = _mutate(
        _routing_record(),
        evidence_citation="https://example.org/tfidf (Documentation 2024)",
    )
    with pytest.raises(ValueError, match="evidence_citation"):
        MethodRegister.model_validate(_single_register(record))
    record = _mutate(
        _routing_record(),
        assumptions="Document boundaries and filtering stay fixed.",
    )
    with pytest.raises(ValueError, match="assumptions"):
        MethodRegister.model_validate(_single_register(record))
    record = _mutate(
        _routing_record(),
        null_calibration="None; review the top terms by hand.",
    )
    with pytest.raises(ValueError, match="null_calibration"):
        MethodRegister.model_validate(_single_register(record))


def test_full_routing_record_round_trips_json() -> None:
    register = MethodRegister.model_validate(_single_register(_routing_record()))
    dump = register.model_dump(mode="json")
    again = MethodRegister.model_validate(dump)
    assert again == register
    assert register.methods[0].question_phrases == [
        "What words come up most often in these documents?"
    ]
    assert register.methods[0].suggests == [
        "Which top terms look meaningless here?",
        "What rare words rank high?",
    ]


def test_routing_axes_populated_on_every_row(register: MethodRegister) -> None:
    """Every real row carries populated routing fields within the authoring bounds."""
    seen: dict[str, str] = {}
    for record in register.methods:
        for name in ROUTING_FIELD_NAMES:
            assert getattr(record, name), f"{record.id} missing {name}"
        phrases = record.question_phrases or []
        assert 5 <= len(phrases) <= 10, f"{record.id} phrase count {len(phrases)}"
        assert all(6 <= len(p.split()) <= 14 for p in phrases), f"{record.id} phrase words"
        suggests = record.suggests or []
        assert 2 <= len(suggests) <= 3, f"{record.id} suggests count"
        assert all(len(s.split()) <= 20 for s in suggests), f"{record.id} suggests words"
        primary = record.sources[0]
        assert " (" in primary and primary.endswith(")"), f"{record.id} primary source annotation"
        for phrase in phrases:
            assert phrase not in seen, f"phrase reused by {record.id} and {seen[phrase]}"
            seen[phrase] = record.id


def test_register_version_bumped_for_progression_schema(register: MethodRegister) -> None:
    """The routing fields bumped the register to 3.0.0, W1 progression to 3.1.0,
    and the W2 table contracts to 3.2.0 (surfaces cache per version)."""
    assert register.register_version == "3.2.0"


# ---------------------------------------------------------------------------
# Progression axes (novice-UX program, wave 1)
# ---------------------------------------------------------------------------

PROGRESSION_FIELD_NAMES = ("difficulty", "concepts", "prerequisites", "next")


def test_progression_vocab_tuples_are_pinned() -> None:
    """The difficulty rungs and the teaching concepts match the W1 brief."""
    assert DIFFICULTY_LEVELS == ("novice", "intermediate", "advanced")
    assert TEACHING_CONCEPTS == (
        "active_learning",
        "classification",
        "clustering",
        "contradiction_detection",
        "entity_extraction",
        "information_retrieval",
        "keyword_rules",
        "qualitative_coding",
        "sentiment_analysis",
        "term_weighting",
        "topic_modeling",
        "vector_similarity",
        "weak_supervision",
    )


@pytest.mark.parametrize("field", PROGRESSION_FIELD_NAMES)
def test_progression_fields_default_to_none(field: str) -> None:
    register = MethodRegister.model_validate(_single_register(_valid_record()))
    assert getattr(register.methods[0], field) is None


def test_unknown_difficulty_rejected() -> None:
    record = _mutate(_valid_record(), difficulty="expert")
    with pytest.raises(ValueError) as excinfo:
        MethodRegister.model_validate(_single_register(record))
    message = str(excinfo.value)
    assert "difficulty" in message
    assert "novice" in message


def test_unknown_concept_rejected() -> None:
    record = _mutate(_valid_record(), concepts=["vibes"])
    with pytest.raises(ValueError) as excinfo:
        MethodRegister.model_validate(_single_register(record))
    message = str(excinfo.value)
    assert "concepts" in message
    assert "clustering" in message


def test_duplicate_concepts_rejected() -> None:
    record = _mutate(_valid_record(), concepts=["clustering", "clustering"])
    with pytest.raises(ValueError, match="duplicate"):
        MethodRegister.model_validate(_single_register(record))


def test_empty_concept_list_rejected() -> None:
    record = _mutate(_valid_record(), concepts=[])
    with pytest.raises(ValueError, match="1 to 5"):
        MethodRegister.model_validate(_single_register(record))


def test_concept_count_cap() -> None:
    record = _mutate(_valid_record(), concepts=sorted(TEACHING_CONCEPTS))
    with pytest.raises(ValueError, match="1 to 5"):
        MethodRegister.model_validate(_single_register(record))


def test_link_id_shape_rejected() -> None:
    record = _mutate(_valid_record(), prerequisites=["4"])
    with pytest.raises(ValueError, match="zero-padded"):
        MethodRegister.model_validate(_single_register(record))
    record = _mutate(_valid_record(), next=["31"])
    with pytest.raises(ValueError, match="31"):
        MethodRegister.model_validate(_single_register(record))


def test_self_prerequisite_rejected() -> None:
    record = _mutate(_valid_record(), id="15", prerequisites=["15"])
    with pytest.raises(ValueError, match="its own"):
        MethodRegister.model_validate(_single_register(record))


def test_self_next_rejected() -> None:
    record = _mutate(_valid_record(), id="15", next=["15"])
    with pytest.raises(ValueError, match="its own"):
        MethodRegister.model_validate(_single_register(record))


def test_dangling_prerequisite_rejected() -> None:
    """A prerequisite must name a registered method id."""
    record = _mutate(_valid_record(), id="15", prerequisites=["99"])
    with pytest.raises(ValueError, match="99"):
        MethodRegister.model_validate(_single_register(record))


def test_dangling_next_rejected() -> None:
    record = _mutate(_valid_record(), id="04", next=["99"])
    with pytest.raises(ValueError, match="99"):
        MethodRegister.model_validate(_single_register(record))


def test_prerequisite_cycle_rejected() -> None:
    """Two methods cannot each require the other."""
    first = _mutate(_valid_record(), id="04", name="A", prerequisites=["15"])
    second = _mutate(_valid_record(), id="15", name="B", prerequisites=["04"])
    with pytest.raises(ValueError, match="cycle"):
        MethodRegister.model_validate(_mini_register(first, second))


def test_next_cycle_rejected() -> None:
    first = _mutate(_valid_record(), id="15", name="A", next=["16"])
    second = _mutate(_valid_record(), id="16", name="B", next=["15"])
    with pytest.raises(ValueError, match="cycle"):
        MethodRegister.model_validate(_mini_register(first, second))


def test_known_progression_pair_round_trips() -> None:
    """A prerequisite edge and its inverse next edge validate and dump cleanly."""
    first = _mutate(
        _valid_record(),
        id="04",
        name="A",
        difficulty="novice",
        concepts=["keyword_rules"],
        next=["15"],
    )
    second = _mutate(
        _valid_record(),
        id="15",
        name="B",
        difficulty="intermediate",
        concepts=["classification", "term_weighting"],
        prerequisites=["04"],
    )
    register = MethodRegister.model_validate(_mini_register(first, second))
    dump = register.model_dump(mode="json")
    again = MethodRegister.model_validate(dump)
    assert again == register
    assert register.methods[1].prerequisites == ["04"]
    assert register.methods[0].next == ["15"]


def test_progression_populated_on_every_row(register: MethodRegister) -> None:
    """Every real row carries a rung and taught concepts within the bounds."""
    for record in register.methods:
        assert record.difficulty in DIFFICULTY_LEVELS, f"{record.id} difficulty"
        assert record.concepts, f"{record.id} concepts"
        assert all(concept in TEACHING_CONCEPTS for concept in record.concepts)
        assert len(record.concepts) <= 5, f"{record.id} concept count"
        for link in (record.prerequisites or []) + (record.next or []):
            assert link in EXPECTED_EXISTING_OPS or link in EXPECTED_NEW_OPS, (
                f"{record.id} names unplanned method {link}"
            )


def test_difficulty_ladder_uses_all_three_rungs(register: MethodRegister) -> None:
    seen = {record.difficulty for record in register.methods}
    assert seen == set(DIFFICULTY_LEVELS)


def test_prerequisite_targets_exist_and_are_first_builds(register: MethodRegister) -> None:
    """Prerequisites point at labeling or classification builds (the data producers)."""
    for record in register.methods:
        for target in record.prerequisites or []:
            target_row = next(row for row in register.methods if row.id == target)
            assert target_row.op in ("labeling_build", "classification_build"), (
                f"{record.id} requires {target} ({target_row.op}); prerequisites must "
                "produce stored data."
            )


def test_next_edges_stay_groundable(register: MethodRegister) -> None:
    """Every next target is an implemented method; none is not_planned."""
    for record in register.methods:
        for target in record.next or []:
            target_row = next(row for row in register.methods if row.id == target)
            assert target_row.implementation_status != "not_planned", (
                f"{record.id} suggests {target}, which is not planned."
            )


# ---------------------------------------------------------------------------
# Table contracts (novice-UX program, wave 2)
# ---------------------------------------------------------------------------


def test_table_column_types_are_pinned() -> None:
    """The column type vocabulary matches the W2 brief."""
    assert COLUMN_TYPES == (
        "text",
        "id",
        "integer",
        "number",
        "boolean",
        "id_list",
        "object_list",
    )


def test_table_defaults_to_none() -> None:
    register = MethodRegister.model_validate(_single_register(_valid_record()))
    assert register.methods[0].output_table is None


def _valid_table() -> dict[str, Any]:
    """One fully valid output_table to mutate in rejection tests."""
    return {
        "columns": [
            {"name": "similarity", "type": "number", "description": "The pair score."},
            {"name": "chunk_id_i", "type": "id", "description": "The lower chunk id."},
        ],
        "order_by": ["-similarity", "chunk_id_i"],
        "ties": "Equal scores order by the lower id.",
        "precision": 6,
        "denominators": ["screened_count"],
        "caveats": [],
    }


def test_valid_table_round_trips_json() -> None:
    record = _mutate(_valid_record(), output_table=_valid_table())
    register = MethodRegister.model_validate(_single_register(record))
    dump = register.model_dump(mode="json")
    again = MethodRegister.model_validate(dump)
    assert again == register
    table = register.methods[0].output_table
    assert table is not None
    assert table.columns[0].unit is None
    assert table.precision == 6


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ({"type": "score"}, "number"),
        ({"name": ""}, "non-empty"),
        ({"description": ""}, "non-empty"),
    ],
)
def test_table_column_rejections_name_the_fix(mutation: dict[str, str], fragment: str) -> None:
    table = _valid_table()
    table["columns"][0] = {**table["columns"][0], **mutation}
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError) as excinfo:
        MethodRegister.model_validate(_single_register(record))
    assert fragment in str(excinfo.value)


def test_table_unit_must_be_text() -> None:
    table = _valid_table()
    table["columns"][0]["unit"] = "   "
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="unit"):
        MethodRegister.model_validate(_single_register(record))


def test_table_unknown_column_rejected() -> None:
    table = _valid_table()
    table["columns"][0] = {**table["columns"][0], "type": "score"}
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="output_table"):
        MethodRegister.model_validate(_single_register(record))


def test_table_unknown_key_rejected() -> None:
    table = {**_valid_table(), "rows_hint": 20}
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="rows_hint"):
        MethodRegister.model_validate(_single_register(record))


def test_table_order_by_must_name_declared_columns() -> None:
    table = _valid_table()
    table["order_by"] = ["-similarity", "chunk_id_j"]
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="chunk_id_j"):
        MethodRegister.model_validate(_single_register(record))


def test_table_order_by_rejects_duplicates_and_emptiness() -> None:
    table = _valid_table()
    table["order_by"] = []
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="order_by"):
        MethodRegister.model_validate(_single_register(record))
    table["order_by"] = ["chunk_id_i", "chunk_id_i"]
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="duplicate"):
        MethodRegister.model_validate(_single_register(record))


def test_table_model_rank_must_stand_alone() -> None:
    """The reserved model_rank entry means the producer's reply order."""
    table = _valid_table()
    table["order_by"] = ["model_rank", "-similarity"]
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="model_rank"):
        MethodRegister.model_validate(_single_register(record))


def test_table_ties_required() -> None:
    table = _valid_table()
    table["ties"] = "   "
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="ties"):
        MethodRegister.model_validate(_single_register(record))


def test_table_precision_bounds() -> None:
    table = _valid_table()
    table["precision"] = -1
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="precision"):
        MethodRegister.model_validate(_single_register(record))
    table["precision"] = 13
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="precision"):
        MethodRegister.model_validate(_single_register(record))


def test_table_denominators_and_caveats_reject_junk() -> None:
    table = _valid_table()
    table["denominators"] = ["screened_count", "screened_count"]
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="duplicate"):
        MethodRegister.model_validate(_single_register(record))
    table["caveats"] = ["same words", "same words"]
    record = _mutate(_valid_record(), output_table=table)
    with pytest.raises(ValueError, match="duplicate"):
        MethodRegister.model_validate(_single_register(record))


TABLE_IDS = {
    "05",
    "06",
    "07",
    "12",
    "13",
    "14",
    "15",
    "18",
    "20",
    "26",
    "27",
    "28",
    "29",
    "30",
}
TABLE_FREE_IDS = {
    "01",
    "02",
    "03",
    "04",
    "08",
    "09",
    "10",
    "11",
    "16",
    "17",
    "19",
    "21",
    "22",
    "23",
    "24",
    "25",
}


def test_real_rows_declare_tables_exactly_on_tabular_methods(
    register: MethodRegister,
) -> None:
    """The 14 tabular-row methods carry a table; the other 16 carry none."""
    declared = {r.id for r in register.methods if r.output_table is not None}
    assert declared == TABLE_IDS
    free = {r.id for r in register.methods if r.output_table is None}
    assert free == TABLE_FREE_IDS


def test_real_tables_are_well_formed(register: MethodRegister) -> None:
    """Every declared table: order keys exist, floats carry precision."""
    for record in register.methods:
        table = record.output_table
        if table is None:
            continue
        names = [column.name for column in table.columns]
        assert len(names) == len(set(names)), f"{record.id} duplicate columns"
        for key in table.order_by:
            assert key.lstrip("-") in names or key == "model_rank", f"{record.id} order {key}"
        if any(column.type == "number" for column in table.columns):
            assert table.precision is not None, f"{record.id} number column without precision"
        for denominator in table.denominators or []:
            assert denominator, f"{record.id} empty denominator"
