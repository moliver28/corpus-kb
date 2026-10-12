"""Wave-2 release gates (U7-U19, U34) — per-gate known-truth tests.

Every gate is evaluated against in-memory evidence fixtures (no database);
known truths: a complete codebook passes, a dangling parent fails, a leaky
partition fails, an uncalibrated agreement score may not route, a raw PPI
estimate is never validated. Deterministic: the alt-test runs with the
gate's fixed seed.
"""

from __future__ import annotations

from corpus_kb.research.release.gate_machine_profile import (
    REQUIRED_MACHINE_PROFILE_FIELDS,
    model_family,
)
from corpus_kb.research.release.gate_routing import AGREEMENT_ECE_CEILING
from corpus_kb.research.release.gate_types import GateInputs
from corpus_kb.research.release.gates import GATES
from corpus_kb.research.release.profiles import (
    EXPLORATORY,
    resolve_profile,
)

TEAM = resolve_profile("team-codebook")
HIGH = resolve_profile("high-assurance")


def run(gate_id: str, inputs: GateInputs, profile=TEAM):
    return GATES[gate_id](inputs, profile)


def complete_code(category_id: str, **overrides) -> dict[str, object]:
    code: dict[str, object] = {
        "id": category_id,
        "brief_definition": "workforce pause longer than 2 seconds",
        "inclusion_criteria": "participant states a pause",
        "exclusion_criteria": "moderator pause",
        "examples": [
            "we paused the rollout",
            {"text": "not sure if that counts", "polarity": "near_miss"},
        ],
    }
    code.update(overrides)
    return code


# ---------------------------------------------------------------------------
# U7 definition_completeness
# ---------------------------------------------------------------------------


def test_u7_complete_codebook_passes():
    doc = {"categories": [complete_code("c1"), complete_code("c2")]}
    result = run("definition_completeness", GateInputs(codebook=doc))
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict) and value["n_codes"] == 2 and value["defects"] == {}


def test_u7_hierarchical_parent_reference_validated():
    doc = {
        "categories": [
            complete_code("parent"),
            complete_code("child", parent_id="parent"),
        ]
    }
    assert run("definition_completeness", GateInputs(codebook=doc)).status == "pass"
    dangling = {
        "categories": [
            complete_code("parent"),
            complete_code("child", parent_id="ghost"),
        ]
    }
    result = run("definition_completeness", GateInputs(codebook=dangling))
    assert result.status == "fail"
    value = result.value
    assert isinstance(value, dict)
    assert any("dangling parent" in d for d in value["defects"]["child"])


def test_u7_flags_missing_near_miss_and_definition():
    doc = {
        "categories": [
            complete_code("c1", brief_definition=None),
            complete_code("c2", examples=["only positive"]),
        ]
    }
    result = run("definition_completeness", GateInputs(codebook=doc))
    assert result.status == "fail"
    value = result.value
    assert isinstance(value, dict)
    assert "missing definition" in value["defects"]["c1"]
    assert "missing near-miss exemplar" in value["defects"]["c2"]


def test_u7_not_evaluable_without_codebook():
    assert run("definition_completeness", GateInputs()).status == "not_evaluable"
    junk = GateInputs(codebook={"categories": ["not-a-dict"]})
    result = run("definition_completeness", junk)
    assert result.status == "not_evaluable"
    assert "invalid_output" in result.reason


# ---------------------------------------------------------------------------
# U8 boundary_agreement
# ---------------------------------------------------------------------------


def _boundary(segments_a, segments_b):
    return {
        "documents": [
            {
                "document_id": "d1",
                "segments_by_coder": {"alice": segments_a, "bob": segments_b},
            }
        ]
    }


def test_u8_identical_segmentations_pass():
    segs = [{"start": 0, "end": 10, "quote": "we paused the rollout"}]
    result = run("boundary_agreement", GateInputs(boundary=_boundary(segs, segs)))
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict)
    assert value["mean_boundary_f1"] == 1.0
    assert "reliability_alpha" in str(value["label_agreement"])


def test_u8_normalized_quote_matches_despite_span_drift():
    a = [{"start": 0, "end": 23, "quote": "we paused the rollout"}]
    b = [{"start": 4, "end": 27, "quote": "we  paused the\u00a0rollout"}]
    result = run("boundary_agreement", GateInputs(boundary=_boundary(a, b)))
    assert result.status == "pass"


def test_u8_divergent_boundaries_fail_below_convention_floor():
    a = [
        {"start": 0, "end": 10, "quote": "alpha segment text"},
        {"start": 20, "end": 30, "quote": "beta segment here"},
    ]
    b = [
        {"start": 0, "end": 10, "quote": "alpha segment text"},
        {"start": 21, "end": 31, "quote": "unrelated words"},
    ]
    result = run("boundary_agreement", GateInputs(boundary=_boundary(a, b)))
    assert result.status == "fail"
    assert result.threshold == 0.80


def test_u8_not_evaluable_with_single_coder():
    solo = {"documents": [{"document_id": "d1", "segments_by_coder": {"alice": []}}]}
    result = run("boundary_agreement", GateInputs(boundary=solo))
    assert result.status == "not_evaluable"
    assert "insufficient_data" in result.reason


# ---------------------------------------------------------------------------
# U9 partition_independence
# ---------------------------------------------------------------------------


def test_u9_disjoint_partitions_pass():
    assignments = [
        {"unit_id": "u1", "partition": "discovery"},
        {"unit_id": "u2", "partition": "calibration"},
        {"unit_id": "u3", "partition": "test"},
        {"unit_id": "u4", "partition": "audit"},
    ]
    result = run(
        "partition_independence",
        GateInputs(partitions={"assignments": assignments, "rule": "disjoint"}),
    )
    assert result.status == "pass"


def test_u9_unit_and_document_leakage_fail():
    unit_leak = {
        "rule": "disjoint",
        "assignments": [
            {"unit_id": "u1", "partition": "discovery"},
            {"unit_id": "u1", "partition": "test"},
        ],
    }
    result = run("partition_independence", GateInputs(partitions=unit_leak))
    assert result.status == "fail"
    value = result.value
    assert isinstance(value, dict)
    assert value["overlaps"]["unit"]
    doc_leak = {
        "rule": "disjoint",
        "assignments": [
            {"unit_id": "u1", "partition": "discovery", "document_id": "d9"},
            {"unit_id": "u2", "partition": "test", "document_id": "d9"},
        ],
    }
    result = run("partition_independence", GateInputs(partitions=doc_leak))
    assert result.status == "fail"
    value = result.value
    assert isinstance(value, dict) and value["overlaps"]["document"]


def test_u9_unknown_partition_label_is_invalid_output():
    bad = {"rule": "disjoint", "assignments": [{"unit_id": "u1", "partition": "training"}]}
    result = run("partition_independence", GateInputs(partitions=bad))
    assert result.status == "fail"
    assert "invalid_output" in result.reason


def test_u9_rule_none_still_reports_disjointness():
    assignments = [{"unit_id": "u1", "partition": "discovery"}]
    result = run(
        "partition_independence",
        GateInputs(partitions={"assignments": assignments, "rule": "none"}),
    )
    assert result.status == "pass"
    assert "not_enforced" in result.reason


# ---------------------------------------------------------------------------
# U10 machine_profile (+U34 coder-panel diversity)
# ---------------------------------------------------------------------------


def machine_profile(**overrides) -> dict[str, object]:
    block: dict[str, object] = {
        "model_id": "qwen3:8b",
        "model_digest": "sha256:abc",
        "runtime_endpoint": "http://ollama:11434",
        "model_family": "qwen",
        "prompt_bundle_sha256": "sha256:prompt",
        "decoding_params": {"temperature": 0.0},
        "seeds": [42],
        "embedder_id": "nomic-embed-text",
        "embedding_dimensions": 768,
        "app_version": "1.2.3",
        "package_versions": {"corpus-kb": "1.2.3"},
        "coder_panel": {
            "proposer": {"model_id": "qwen3:8b"},
            "validator": {"model_id": "llama3:8b"},
        },
    }
    block.update(overrides)
    return block


def test_model_family_mapping_is_the_documented_heuristic():
    assert model_family("qwen3:8b") == "qwen"
    assert model_family("meta-llama/Llama-3-8B") == "llama"
    assert model_family("gpt-4o") == "openai"
    assert model_family("mystery-model") == "unknown"


def test_u10_u34_high_assurance_requires_family_diverse_panel():
    inputs = GateInputs(manifest={"machine_profile": machine_profile()})
    assert run("machine_profile", inputs, HIGH).status == "pass"
    same_family = machine_profile(
        coder_panel={
            "proposer": {"model_id": "qwen3:8b"},
            "validator": {"model_id": "qwen2.5:7b"},
        }
    )
    result = run("machine_profile", GateInputs(manifest={"machine_profile": same_family}), HIGH)
    assert result.status == "fail"
    assert "U34" in result.reason


def test_u34_team_codebook_warns_but_does_not_fail_on_same_family():
    same_family = machine_profile(
        coder_panel={
            "proposer": {"model_id": "qwen3:8b"},
            "validator": {"model_id": "qwen2.5:7b"},
        }
    )
    result = run("machine_profile", GateInputs(manifest={"machine_profile": same_family}), TEAM)
    assert result.status == "pass"
    assert "warn" in result.reason


def test_u10_missing_fields_fail_when_required_not_evaluable_otherwise():
    incomplete = machine_profile(model_digest=None, seeds=None)
    result = run("machine_profile", GateInputs(manifest={"machine_profile": incomplete}), HIGH)
    assert result.status == "fail"
    value = result.value
    assert isinstance(value, dict)
    assert sorted(value["missing"]) == ["model_digest", "seeds"]
    result = run(
        "machine_profile", GateInputs(manifest={"machine_profile": incomplete}), EXPLORATORY
    )
    assert result.status == "not_evaluable"
    assert len(REQUIRED_MACHINE_PROFILE_FIELDS) == 11


# ---------------------------------------------------------------------------
# U11 heldout_per_code_f1 / U15 stratum_disparity
# ---------------------------------------------------------------------------


def heldout_block(**c1_overrides) -> dict[str, object]:
    c1: dict[str, object] = {
        "code_id": "c1",
        "tp": 18,
        "fp": 2,
        "fn": 2,
        "strata": [
            {"stratum": "interview", "tp": 9, "fp": 1, "fn": 1},
            {"stratum": "forum", "tp": 9, "fp": 1, "fn": 1},
        ],
    }
    c1.update(c1_overrides)
    return {"codes": [c1, {"code_id": "c2", "tp": 5, "fp": 5, "fn": 5}]}


def test_u11_per_code_f1_floor_enforced_and_strata_reported():
    result = run("heldout_per_code_f1", GateInputs(heldout=heldout_block()))
    assert result.status == "fail"  # c2: tp=5, fp=5, fn=5 -> F1 0.5 < 0.70
    assert result.threshold == TEAM.per_code_f1_floor
    value = result.value
    assert isinstance(value, dict)
    assert value["failing_codes"] == ["c2"]
    c1 = value["per_code"][0]
    assert c1["precision"] == 0.9 and c1["recall"] == 0.9
    assert len(c1["strata"]) == 2  # per-stratum breakdown carried in evidence


def test_u11_unmeasured_codes_are_listed_never_hidden():
    block = heldout_block(strata=[], tp=None)
    result = run("heldout_per_code_f1", GateInputs(heldout=block))
    value = result.value
    assert isinstance(value, dict)
    assert "c1" in value["unmeasured_codes"]


def test_u11_not_enforced_without_floor():
    result = run("heldout_per_code_f1", GateInputs(heldout=heldout_block()), EXPLORATORY)
    assert result.status == "pass"
    assert result.reason.startswith("not_enforced")


def test_u15_disparity_within_and_beyond_limit():
    within = heldout_block()
    result = run("stratum_disparity", GateInputs(heldout=within), TEAM)
    assert result.status == "pass"
    skewed = heldout_block(
        strata=[
            {"stratum": "interview", "tp": 9, "fp": 1, "fn": 1},
            {"stratum": "forum", "tp": 2, "fp": 8, "fn": 0},
        ]
    )
    result = run("stratum_disparity", GateInputs(heldout=skewed), TEAM)
    assert result.status == "fail"
    assert result.threshold == TEAM.max_stratum_disparity


def test_u15_small_strata_not_evaluable():
    tiny = heldout_block(
        strata=[
            {"stratum": "interview", "tp": 1, "fp": 0, "fn": 0},
            {"stratum": "forum", "tp": 0, "fp": 1, "fn": 1},
        ]
    )
    result = run("stratum_disparity", GateInputs(heldout=tiny), TEAM)
    assert result.status == "not_evaluable"
    value = result.value
    assert isinstance(value, dict)
    assert value["excluded_small_strata"] == 2


# ---------------------------------------------------------------------------
# reliability_alpha
# ---------------------------------------------------------------------------


def test_reliability_alpha_pass_floor_is_tentative_convention():
    report = {
        "overall": {"alpha": 0.82},
        "per_code": {"c1": {"alpha": 0.9}, "c2": {"alpha": 0.5}},
    }
    result = run("reliability_alpha", GateInputs(reliability=report))
    assert result.status == "pass"
    assert result.threshold == TEAM.alpha_tentative
    value = result.value
    assert isinstance(value, dict)
    assert value["meets_reliable_convention"] is True
    assert value["halt_band_codes"] == ["c2"]
    assert "warn" in result.reason


def test_reliability_alpha_fails_below_tentative():
    report = {"overall": {"alpha": 0.4}, "per_code": {}}
    result = run("reliability_alpha", GateInputs(reliability=report))
    assert result.status == "fail"


# ---------------------------------------------------------------------------
# U12 prompt_stability / U13 alt test / U14 PPI
# ---------------------------------------------------------------------------


def test_u12_identical_variants_pass_divergent_fail():
    same = {
        "variant_label_sets": [
            {"variant": "v1", "labels": {"u1": "x", "u2": "y"}},
            {"variant": "v2", "labels": {"u1": "x", "u2": "y"}},
        ]
    }
    result = run("prompt_stability", GateInputs(prompt_variants=same))
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict) and value["alpha"] == 1.0
    divergent = {
        "variant_label_sets": [
            {"variant": "v1", "labels": {f"u{i}": "x" for i in range(6)}},
            {"variant": "v2", "labels": {f"u{i}": "y" for i in range(6)}},
        ]
    }
    result = run("prompt_stability", GateInputs(prompt_variants=divergent))
    assert result.status == "fail"
    assert result.threshold == TEAM.prompt_stability_alpha


def test_u12_single_variant_not_evaluable():
    thin = {"variant_label_sets": [{"variant": "v1", "labels": {"u1": "x"}}]}
    result = run("prompt_stability", GateInputs(prompt_variants=thin))
    assert result.status == "not_evaluable"
    assert "insufficient_data" in result.reason


def _annotators(model_wins: bool) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    annotations: dict[str, dict[str, str]] = {}
    preds: dict[str, str] = {}
    for i in range(20):
        unit = f"u{i}"
        a3 = "x" if i < 10 else "y"
        a4 = "x" if i < 8 else "y"
        annotations[unit] = {"a1": "x", "a2": "x", "a3": a3, "a4": a4}
        preds[unit] = "x" if model_wins else "y"
    return annotations, preds


def test_u13_model_matching_majority_passes():
    annotations, preds = _annotators(model_wins=True)
    result = run(
        "alternative_annotator_test",
        GateInputs(alt_test_data={"annotations": annotations, "model_preds": preds}),
    )
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict)
    assert value["winning_rate"] >= TEAM.alt_test_passing_fraction


def test_u13_contrarian_model_fails():
    annotations, preds = _annotators(model_wins=False)
    result = run(
        "alternative_annotator_test",
        GateInputs(alt_test_data={"annotations": annotations, "model_preds": preds}),
    )
    assert result.status == "fail"


def test_u13_deterministic_under_fixed_seed():
    annotations, preds = _annotators(model_wins=True)
    inputs = GateInputs(alt_test_data={"annotations": annotations, "model_preds": preds})
    first = run("alternative_annotator_test", inputs)
    second = run("alternative_annotator_test", inputs)
    assert first.value == second.value


def test_u13_insufficient_without_multiply_annotated_units():
    thin = {"annotations": {"u1": {"a1": "x"}}, "model_preds": {"u1": "x"}}
    result = run("alternative_annotator_test", GateInputs(alt_test_data=thin))
    assert result.status == "not_evaluable"
    assert "insufficient_data" in result.reason


def test_u14_corrected_estimate_reports_raw_beside_debiased():
    block = {
        "model_preds_all": [0.9] * 10 + [0.1] * 10,
        "human_labels_sample": [1, 0, 1, 1, 0],
        "model_preds_audited": [0.9, 0.1, 0.9, 0.9, 0.1],
        "inclusion_probabilities": [0.5, 0.5, 0.5, 0.5, 0.5],
    }
    result = run("prediction_powered_estimates", GateInputs(ppi=block), HIGH)
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict)
    assert value["label"] == "corrected"
    assert value["raw_is_validated"] is False
    assert value["debiased_estimate"] != value["raw_estimate"]
    assert value["lower"] <= value["debiased_estimate"] <= value["upper"]


def test_u14_without_audit_sample_raw_stays_uncorrected_and_not_evaluable():
    block = {"model_preds_all": [0.9, 0.1], "human_labels_sample": []}
    result = run("prediction_powered_estimates", GateInputs(ppi=block), HIGH)
    assert result.status == "not_evaluable"
    value = result.value
    assert isinstance(value, dict)
    assert value["label"] == "uncorrected"
    assert value["raw_is_validated"] is False


def test_u14_misaligned_inputs_are_invalid_output_not_a_pass():
    block = {
        "model_preds_all": [0.9],
        "human_labels_sample": [1, 0],
        "model_preds_audited": [0.9],
        "inclusion_probabilities": [0.5, 0.5],
    }
    result = run("prediction_powered_estimates", GateInputs(ppi=block), HIGH)
    assert result.status == "not_evaluable"
    assert "invalid_output" in result.reason


# ---------------------------------------------------------------------------
# U16 content_stability / U17 audit_sample_design
# ---------------------------------------------------------------------------


def stability_block(overlap: bool) -> dict[str, object]:
    shared = [f"t{i}" for i in range(10)]
    stable = [*shared, "extra_a"]
    drifted = [*shared, "extra_b"] if overlap else ["x1", "x2", *shared[2:]]
    runs = [stable, drifted]
    return {"runs": runs, "membership_ari": 0.91, "k": 10}


def test_u16_content_gates_membership_reports():
    result = run("content_stability", GateInputs(stability=stability_block(overlap=True)))
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict)
    assert value["membership_ari"] == 0.91
    assert "separately" in str(value["membership_note"])
    divergent = {"runs": [["a", "b", "c"], ["d", "e", "f"]], "membership_ari": 0.2, "k": 3}
    result = run("content_stability", GateInputs(stability=divergent))
    assert result.status == "fail"
    assert result.threshold == TEAM.content_stability_floor


def test_u16_single_run_not_evaluable():
    result = run("content_stability", GateInputs(stability={"runs": [["a"]]}))
    assert result.status == "not_evaluable"
    assert "insufficient_data" in result.reason


def audit_rows(n: int, **row_overrides) -> dict[str, object]:
    rows = [
        {
            "unit_id": f"u{i}",
            "partition": "audit",
            "stratum": "c1|low",
            "inclusion_probability": 0.1,
            "seed": 42,
        }
        for i in range(n)
    ]
    if row_overrides:
        rows.append({"unit_id": "bad", "partition": "audit", **row_overrides})
    return {"rows": rows}


def test_u17_design_complete_passes_and_meets_minimum():
    result = run("audit_sample_design", GateInputs(audit_sample=audit_rows(40)))
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict)
    assert value["seeds"] == [42]
    assert value["inclusion_probability_range"] == [0.1, 0.1]


def test_u17_below_profile_minimum_fails():
    result = run("audit_sample_design", GateInputs(audit_sample=audit_rows(40)), HIGH)
    assert result.status == "fail"
    assert "insufficient_data" in result.reason
    assert result.threshold == HIGH.min_audit_sample


def test_u17_incomplete_design_rows_fail():
    broken = audit_rows(40, stratum=None, inclusion_probability=1.5, seed=None)
    result = run("audit_sample_design", GateInputs(audit_sample=broken))
    assert result.status == "fail"
    assert "invalid_output" in result.reason


def test_u17_no_sample_not_evaluable():
    assert run("audit_sample_design", GateInputs()).status == "not_evaluable"


# ---------------------------------------------------------------------------
# U18 agreement_calibration / U19 agreement_routing
# ---------------------------------------------------------------------------


def calibrated_scores() -> dict[str, list[float]]:
    # Low-agreement bin: score 0.2, empirical accuracy 0.2; high bin: score
    # 0.9, empirical accuracy 0.8 -> ECE 0.05 <= ceiling, AUROC ~0.95.
    scores = [0.2] * 10 + [0.9] * 10
    outcomes = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0]
    return {"scores": scores, "outcomes": outcomes}


def test_u18_validated_agreement_score_passes():
    result = run(
        "agreement_calibration", GateInputs(agreement_calibration=calibrated_scores()), HIGH
    )
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict)
    assert value["auroc"] is not None and value["auroc"] >= 0.55
    assert value["ece"] <= AGREEMENT_ECE_CEILING


def test_u18_discriminating_but_miscalibrated_score_may_not_route():
    block = calibrated_scores()
    block["scores"] = [0.95] * 10 + [0.95] * 10  # constant high score: no discrimination
    result = run("agreement_calibration", GateInputs(agreement_calibration=block), HIGH)
    assert result.status == "fail"


def test_u18_single_class_outcomes_not_evaluable():
    block = {"scores": [0.5] * 10, "outcomes": [1] * 10}
    result = run("agreement_calibration", GateInputs(agreement_calibration=block), HIGH)
    assert result.status == "not_evaluable"
    assert "insufficient_data" in result.reason


def test_u19_unrouted_low_agreement_units_fail():
    ledger = {
        "units": [
            {"unit_id": "u1", "agreement": 0.5, "routed": False},
            {"unit_id": "u2", "agreement": 0.95, "routed": False},
        ]
    }
    result = run("agreement_routing", GateInputs(agreement_routing=ledger), TEAM)
    assert result.status == "fail"
    value = result.value
    assert isinstance(value, dict)
    assert value["unrouted_unit_ids"] == ["u1"]
    assert value["n_below_threshold"] == 1


def test_u19_routed_queue_passes_and_exposes_the_list():
    ledger = {
        "units": [
            {"unit_id": "u1", "agreement": 0.5, "routed": True},
            {"unit_id": "u2", "agreement": 0.95, "routed": False},
        ]
    }
    result = run("agreement_routing", GateInputs(agreement_routing=ledger), TEAM)
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict)
    assert [u["unit_id"] for u in value["low_agreement_units"]] == ["u1"]
    assert result.threshold == TEAM.agreement_routing_threshold


def test_u19_not_enforced_without_profile_threshold():
    ledger = {"units": [{"unit_id": "u1", "agreement": 0.1, "routed": False}]}
    result = run("agreement_routing", GateInputs(agreement_routing=ledger), EXPLORATORY)
    assert result.status == "pass"
    assert result.reason.startswith("not_enforced")


# ---------------------------------------------------------------------------
# Exhaustiveness plateau / U36 saturation
# ---------------------------------------------------------------------------


def test_exhaustiveness_plateau_gate():
    plateau = GateInputs(
        metrics={"exhaustiveness": {"coverage_slope": 0.001, "pooled": {"R": 0.12}}}
    )
    assert run("exhaustiveness", plateau).status == "pass"
    moving = GateInputs(metrics={"exhaustiveness": {"coverage_slope": -0.2}})
    result = run("exhaustiveness", moving)
    assert result.status == "fail"
    assert run("exhaustiveness", GateInputs()).status == "not_evaluable"


def test_u36_saturation_run_stop_and_curve():
    reached = GateInputs(
        metrics={
            "saturation": {
                "new_codes": 2,
                "base_unique": 80,
                "unit_code_sets": [["c1", "c2"]] * 50,
            }
        }
    )
    result = run("saturation", reached)
    assert result.status == "pass"
    value = result.value
    assert isinstance(value, dict)
    assert value["curve_final_isr"] == 0.02
    not_reached = GateInputs(metrics={"saturation": {"new_codes": 10, "base_unique": 80}})
    result = run("saturation", not_reached)
    assert result.status == "fail"
    assert run("saturation", GateInputs()).status == "not_evaluable"


# ---------------------------------------------------------------------------
# cycle-side U19 wire-in: gray-zone halt from low agreement
# ---------------------------------------------------------------------------


def test_cycle_gray_zone_from_agreement_raises_existing_halt_gate():
    from corpus_kb.research.cycle_gates import (
        GATE_GRAY_ZONE_ESCALATION,
        gate_gray_zone_from_agreement,
    )

    low = [
        {"unit_id": "u1", "agreement": 0.4},
        {"unit_id": "u2", "agreement": 0.6},
    ]
    finding = gate_gray_zone_from_agreement(low, ["u2"], threshold=0.8)
    assert finding is not None
    assert finding.gate == GATE_GRAY_ZONE_ESCALATION
    assert finding.detail["unit_ids"] == ["u1"]
    assert finding.detail["threshold"] == 0.8


def test_cycle_gray_zone_from_agreement_quiet_when_all_routed_or_unenforced():
    from corpus_kb.research.cycle_gates import gate_gray_zone_from_agreement

    low = [{"unit_id": "u1", "agreement": 0.4}]
    assert gate_gray_zone_from_agreement(low, ["u1"], threshold=0.8) is None
    assert gate_gray_zone_from_agreement(low, [], threshold=None) is None
    assert gate_gray_zone_from_agreement([], [], threshold=0.8) is None


# ---------------------------------------------------------------------------
# assess integration: the team-codebook bundle can go fully green
# ---------------------------------------------------------------------------


def full_green_inputs() -> GateInputs:
    return GateInputs(
        manifest={"machine_profile": machine_profile()},
        canary={"status": "enforced", "first_attempt_valid_rate": 1.0},
        determinism={
            "embedding_matrix_sha256": "a",
            "seed": 1,
            "params": {},
            "library_versions": {},
            "sweep": {"converged": True},
        },
        consensus={"n_runs": 3, "themes_kept": 2, "quote_not_found_rejected": 1},
        codebook={"categories": [complete_code("c1"), complete_code("c2")]},
        boundary=_boundary(
            [{"start": 0, "end": 10, "quote": "we paused the rollout"}],
            [{"start": 0, "end": 10, "quote": "we paused the rollout"}],
        ),
        partitions={
            "rule": "disjoint",
            "assignments": [
                {"unit_id": "u1", "partition": "discovery"},
                {"unit_id": "u2", "partition": "calibration"},
                {"unit_id": "u3", "partition": "test"},
                {"unit_id": "u4", "partition": "audit"},
            ],
        },
        reliability={"overall": {"alpha": 0.85}, "per_code": {"c1": {"alpha": 0.85}}},
        stability=stability_block(overlap=True),
        metrics={
            "exhaustiveness": {"coverage_slope": 0.0},
            "saturation": {"new_codes": 1, "base_unique": 60},
        },
    )


def test_assess_team_codebook_can_pass_without_waivers():
    from corpus_kb.research.release.gates import assess

    bundle = assess(full_green_inputs(), TEAM)
    assert bundle["all_required_pass_without_waivers"] is True
    assert bundle["required_failing"] == []
    assert bundle["required_not_evaluable"] == []
    assert len(bundle["gate_results"]) == 19
    for entry in bundle["gate_results"]:
        assert set(entry) == {"gate_id", "status", "reason", "value", "threshold", "evidence_refs"}
        assert entry["status"] in ("pass", "fail", "not_evaluable")


def test_assess_leak_fails_partition_gate_in_bundle():
    from corpus_kb.research.release.gates import assess

    inputs = full_green_inputs()
    object.__setattr__(
        inputs,
        "partitions",
        {
            "rule": "disjoint",
            "assignments": [
                {"unit_id": "u1", "partition": "discovery"},
                {"unit_id": "u1", "partition": "test"},
            ],
        },
    )
    bundle = assess(inputs, TEAM)
    assert "partition_independence" in bundle["required_failing"]
    assert bundle["all_required_pass_without_waivers"] is False


def test_assess_exploratory_requires_nothing():
    from corpus_kb.research.release.gates import assess

    bundle = assess(GateInputs(), EXPLORATORY)
    assert bundle["required_gate_ids"] == []
    assert bundle["all_required_pass_without_waivers"] is True


# ---------------------------------------------------------------------------
# assess integration: the high-assurance bundle can go fully green too —
# every HIGH required gate is evaluable and passable from recorded inputs
# ---------------------------------------------------------------------------


def stable_variants() -> dict[str, object]:
    """Two identical variant label-sets: pooled alpha 1.0 (HIGH floor 0.80)."""
    return {
        "variant_label_sets": [
            {"variant": "v1", "labels": {"u1": "x", "u2": "y"}},
            {"variant": "v2", "labels": {"u1": "x", "u2": "y"}},
        ]
    }


def heldout_high() -> dict[str, object]:
    """Both strata n=55 (>= min_stratum_size 20), F1 0.9 in each."""
    return {
        "codes": [
            {
                "code_id": "c1",
                "tp": 90,
                "fp": 10,
                "fn": 10,
                "strata": [
                    {"stratum": "interview", "tp": 45, "fp": 5, "fn": 5},
                    {"stratum": "forum", "tp": 45, "fp": 5, "fn": 5},
                ],
            }
        ]
    }


def alt_test_high() -> dict[str, object]:
    """Five annotators, each the lone dissenter on their round-robin units;
    the model reproduces the consensus and beats every annotator (paper
    §3.2 paired scores: on a dissenter's unit the model aligns with all four
    remaining annotators, the dissenter with none) -> winning rate 1.0 >= 0.8.
    """

    def truth(i: int) -> str:
        return "x" if (i // 5) % 2 == 0 else "y"

    annotations = {
        f"u{i}": {f"a{k}": ("z" if f"a{k}" == f"a{i % 5}" else truth(i)) for k in range(5)}
        for i in range(60)
    }
    return {"annotations": annotations, "model_preds": {f"u{i}": truth(i) for i in range(60)}}


def high_green_inputs() -> GateInputs:
    green = full_green_inputs()
    object.__setattr__(green, "heldout", heldout_high())
    # HIGH content_stability_floor is 0.85; the shared block's 10/12 Jaccard
    # (0.833) fails it, so the HIGH bundle uses two identical runs.
    object.__setattr__(
        green,
        "stability",
        {"runs": [[f"t{i}" for i in range(10)]] * 2, "membership_ari": 1.0, "k": 10},
    )
    object.__setattr__(green, "prompt_variants", stable_variants())
    object.__setattr__(green, "alt_test_data", alt_test_high())
    object.__setattr__(
        green,
        "ppi",
        {
            "model_preds_all": [0.9] * 10 + [0.1] * 10,
            "human_labels_sample": [1, 0, 1, 1, 0],
            "model_preds_audited": [0.9, 0.1, 0.9, 0.9, 0.1],
            "inclusion_probabilities": [0.5, 0.5, 0.5, 0.5, 0.5],
        },
    )
    object.__setattr__(green, "audit_sample", audit_rows(100))
    object.__setattr__(green, "agreement_calibration", calibrated_scores())
    return green


def test_assess_high_assurance_can_pass_without_waivers():
    from corpus_kb.research.release.gates import assess

    bundle = assess(high_green_inputs(), HIGH)
    assert bundle["required_failing"] == []
    assert bundle["required_not_evaluable"] == []
    assert bundle["all_required_pass_without_waivers"] is True
    assert len(bundle["required_gate_ids"]) == len(HIGH.required_gates)


def test_assess_high_assurance_blocks_on_missing_u17_audit_design():
    from corpus_kb.research.release.gates import assess

    inputs = high_green_inputs()
    object.__setattr__(inputs, "audit_sample", audit_rows(99))
    bundle = assess(inputs, HIGH)
    assert "audit_sample_design" in bundle["required_failing"]
    assert bundle["all_required_pass_without_waivers"] is False
