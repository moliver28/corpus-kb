"""Release gate framework (U4) — registry, stubs, evidence gates, assess."""

from __future__ import annotations

import pytest

from corpus_kb.research.release.evidence_gates import (
    gate_clustering_determinism,
    gate_quote_consensus,
    gate_structured_output,
)
from corpus_kb.research.release.gate_types import WAVE2_GATES
from corpus_kb.research.release.gates import (
    ALL_RELEASE_GATES,
    STUB_REASON,
    GateInputs,
    GateResult,
    UnknownGateError,
    assess,
)
from corpus_kb.research.release.profiles import (
    DEFAULT_PROFILE,
    ENFORCEMENT_WARN,
    PROFILE_EXPLORATORY,
    PROFILE_HIGH_ASSURANCE,
    PROFILES,
    RELEASE_CONFIG_DEFAULTS,
    ResolvedProfile,
    UnknownProfileError,
    resolve_profile,
)

TEAM = resolve_profile("team-codebook")


def test_registry_covers_the_full_set_and_stubs_are_not_evaluable():
    from corpus_kb.research.release.gates import GATES, WAVE2_GATES

    assert len(GATES) == len(ALL_RELEASE_GATES)
    for gate_id in WAVE2_GATES:
        result = GATES[gate_id](GateInputs(), TEAM)
        assert result.status == "not_evaluable"
        assert result.reason == STUB_REASON


def test_all_profiles_require_only_registered_gates():
    for profile in PROFILES.values():
        unknown = [g for g in profile.required_gates if g not in ALL_RELEASE_GATES]
        assert not unknown, f"{profile.name} requires unknown gates: {unknown}"


def test_profiles_shapes_and_defaults():
    assert DEFAULT_PROFILE == "team-codebook"
    assert resolve_profile(None).name == DEFAULT_PROFILE
    for profile in PROFILES.values():
        assert profile.enforcement == ENFORCEMENT_WARN
        assert 0.0 < profile.alpha_tentative < profile.alpha_reliable <= 1.0
    assert resolve_profile(PROFILE_EXPLORATORY).required_gates == ()
    high = resolve_profile(PROFILE_HIGH_ASSURANCE)
    assert "audit_sample_design" in high.required_gates
    assert high.min_audit_sample > resolve_profile("team-codebook").min_audit_sample
    with pytest.raises(UnknownProfileError):
        resolve_profile("nope")


def test_release_config_defaults_shape():
    block = RELEASE_CONFIG_DEFAULTS["release"]
    assert isinstance(block, dict)
    assert block["default_profile"] == "team-codebook"
    assert block["enforcement"] == "warn"
    profiles = block["profiles"]
    assert isinstance(profiles, dict)
    assert set(profiles) == set(PROFILES)


def test_structured_output_gate_paths():
    enforced = GateInputs(canary={"status": "enforced", "first_attempt_valid_rate": 1.0})
    assert gate_structured_output(enforced, TEAM).status == "pass"
    unknown = GateInputs(canary={"status": "unknown"})
    assert gate_structured_output(unknown, TEAM).status == "not_evaluable"
    missing = GateInputs()
    assert gate_structured_output(missing, TEAM).status == "not_evaluable"
    broken = GateInputs(canary={"status": "not_enforced"})
    result = gate_structured_output(broken, TEAM)
    assert result.status == "fail"
    assert "not_enforced" in result.reason


def test_clustering_determinism_gate_paths():
    assert gate_clustering_determinism(GateInputs(), TEAM).status == "not_evaluable"
    partial = GateInputs(
        determinism={
            "embedding_matrix_sha256": "a",
            "seed": 1,
            "params": {},
            "library_versions": {},
        }
    )
    result = gate_clustering_determinism(partial, TEAM)
    assert result.status == "not_evaluable"
    assert "no converged seed sweep" in result.reason
    not_converged = GateInputs(
        determinism={
            "embedding_matrix_sha256": "a",
            "seed": 1,
            "params": {},
            "library_versions": {},
            "sweep": {"converged": False},
        }
    )
    assert gate_clustering_determinism(not_converged, TEAM).status == "fail"
    converged = GateInputs(
        determinism={
            "embedding_matrix_sha256": "a",
            "seed": 1,
            "params": {},
            "library_versions": {},
            "sweep": {"converged": True},
        }
    )
    assert gate_clustering_determinism(converged, TEAM).status == "pass"


def test_quote_consensus_gate_paths():
    assert gate_quote_consensus(GateInputs(), TEAM).status == "not_evaluable"
    thin = GateInputs(consensus={"n_runs": 1, "themes_kept": 1})
    result = gate_quote_consensus(thin, TEAM)
    assert result.status == "not_evaluable"
    assert "insufficient_data" in result.reason
    empty = GateInputs(consensus={"n_runs": 3, "themes_kept": 0})
    assert gate_quote_consensus(empty, TEAM).status == "fail"
    good = GateInputs(consensus={"n_runs": 3, "themes_kept": 4, "quote_not_found_rejected": 2})
    passed = gate_quote_consensus(good, TEAM)
    assert passed.status == "pass"
    value = passed.value
    assert isinstance(value, dict) and value["quote_not_found_rejected"] == 2


def test_assess_composes_every_gate_and_counts_blockers():
    inputs = GateInputs(
        canary={"status": "enforced"},
        determinism={
            "embedding_matrix_sha256": "a",
            "seed": 1,
            "params": {},
            "library_versions": {},
            "sweep": {"converged": True},
        },
        consensus={"n_runs": 3, "themes_kept": 2},
    )
    bundle = assess(inputs, TEAM)
    results = bundle["gate_results"]
    assert isinstance(results, list) and len(results) == len(ALL_RELEASE_GATES)
    assert bundle["required_failing"] == []
    # The team-codebook profile requires wave-2 stubs: honestly not_evaluable.
    assert set(bundle["required_not_evaluable"]) <= set(WAVE2_GATES)
    assert bundle["all_required_pass_without_waivers"] is False
    assert bundle["profile"] == "team-codebook"


def test_assess_flags_failing_required_gates_and_rejects_unknown():
    failing = GateInputs(
        canary={"status": "not_enforced"},
        determinism={
            "embedding_matrix_sha256": "a",
            "seed": 1,
            "params": {},
            "library_versions": {},
            "sweep": {"converged": False},
        },
        consensus={"n_runs": 3, "themes_kept": 0},
    )
    bundle = assess(failing, TEAM)
    assert "structured_output_enforcement" in bundle["required_failing"]
    assert "clustering_determinism" in bundle["required_failing"]
    assert "quote_consensus" not in bundle["required_failing"]
    broken = ResolvedProfile(name="broken", required_gates=("no_such_gate",))
    with pytest.raises(UnknownGateError):
        assess(GateInputs(), broken)


def test_gate_result_is_honest_value_object():
    result = GateResult(gate_id="g", status="pass", reason="ok", evidence_refs=("a",))
    assert result.status == "pass"
    assert result.value is None and result.threshold is None
