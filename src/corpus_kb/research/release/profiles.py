"""Release profiles (U3, v6 §6) — exploratory / team-codebook / high-assurance.

A profile resolves WHICH release gates are required and the thresholds the
Wave-2 gate functions compare against. Threshold values here are CONVENTIONS
documented as such (alpha 0.667 tentative / 0.80 reliable follows common
qualitative-research practice), never claimed standards.

``enforcement`` semantics (v8 ground rule 6, default ``warn``):
  * off      — release evidence is recorded but nothing blocks;
  * warn     — failing/not_evaluable required gates print explicit warnings
               and still block ``release`` only via the human approver;
  * enforce  — ``release`` refuses unless every required gate is pass|waived.

``RELEASE_CONFIG_DEFAULTS`` (exported as ``DEFAULTS``) is the YAML-shaped
``research.release.*`` block the orchestrator wires into both config.yaml
locations; the typed profiles are the single source of truth for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PROFILE_EXPLORATORY = "exploratory"
PROFILE_TEAM_CODEBOOK = "team-codebook"
PROFILE_HIGH_ASSURANCE = "high-assurance"

ENFORCEMENT_OFF = "off"
ENFORCEMENT_WARN = "warn"
ENFORCEMENT_ENFORCE = "enforce"
ENFORCEMENT_MODES = (ENFORCEMENT_OFF, ENFORCEMENT_WARN, ENFORCEMENT_ENFORCE)


@dataclass(frozen=True)
class ResolvedProfile:
    """One named release profile: required gates + comparison thresholds.

    Threshold fields left ``None`` mean the gate is not measured by this
    profile (its gate id is simply absent from ``required_gates``); a gate
    may still run and report when its inputs exist.
    """

    name: str
    required_gates: tuple[str, ...] = ()
    alpha_tentative: float = 0.667
    alpha_reliable: float = 0.80
    per_code_f1_floor: float | None = None
    content_stability_floor: float | None = None
    prompt_stability_alpha: float | None = None
    alt_test_margin: float | None = None
    alt_test_passing_fraction: float | None = None
    min_audit_sample: int = 0
    max_stratum_disparity: float | None = None
    min_stratum_size: int = 0
    agreement_routing_threshold: float | None = None
    partition_rule: str = "none"
    enforcement: str = ENFORCEMENT_WARN
    notes: tuple[str, ...] = field(default_factory=tuple)


EXPLORATORY = ResolvedProfile(
    name=PROFILE_EXPLORATORY,
    required_gates=(),
    enforcement=ENFORCEMENT_WARN,
    notes=(
        "requires almost nothing; results carry explicit uncorrected/"
        "not_evaluable conditions and are never labeled validated",
    ),
)

TEAM_CODEBOOK = ResolvedProfile(
    name=PROFILE_TEAM_CODEBOOK,
    required_gates=(
        "definition_completeness",
        "machine_profile",
        "partition_independence",
        "reliability_alpha",
        "content_stability",
        "clustering_determinism",
        "structured_output_enforcement",
    ),
    alpha_tentative=0.667,
    alpha_reliable=0.80,
    per_code_f1_floor=0.70,
    content_stability_floor=0.75,
    prompt_stability_alpha=0.667,
    alt_test_margin=0.05,
    alt_test_passing_fraction=0.5,
    min_audit_sample=30,
    max_stratum_disparity=0.15,
    min_stratum_size=10,
    agreement_routing_threshold=0.80,
    partition_rule="disjoint",
    enforcement=ENFORCEMENT_WARN,
)

HIGH_ASSURANCE = ResolvedProfile(
    name=PROFILE_HIGH_ASSURANCE,
    required_gates=(
        "definition_completeness",
        "machine_profile",
        "partition_independence",
        "reliability_alpha",
        "heldout_per_code_f1",
        "content_stability",
        "prompt_stability",
        "alternative_annotator_test",
        "prediction_powered_estimates",
        "stratum_disparity",
        "audit_sample_design",
        "agreement_calibration",
        "boundary_agreement",
        "clustering_determinism",
        "structured_output_enforcement",
        "quote_consensus",
    ),
    alpha_tentative=0.667,
    alpha_reliable=0.80,
    per_code_f1_floor=0.80,
    content_stability_floor=0.85,
    prompt_stability_alpha=0.80,
    alt_test_margin=0.05,
    alt_test_passing_fraction=0.8,
    min_audit_sample=100,
    max_stratum_disparity=0.10,
    min_stratum_size=20,
    agreement_routing_threshold=0.90,
    partition_rule="disjoint",
    enforcement=ENFORCEMENT_WARN,
)

PROFILES: dict[str, ResolvedProfile] = {
    PROFILE_EXPLORATORY: EXPLORATORY,
    PROFILE_TEAM_CODEBOOK: TEAM_CODEBOOK,
    PROFILE_HIGH_ASSURANCE: HIGH_ASSURANCE,
}

DEFAULT_PROFILE = PROFILE_TEAM_CODEBOOK


class UnknownProfileError(ValueError):
    """A release profile name is not one of the three defined profiles."""


def resolve_profile(name: str | None) -> ResolvedProfile:
    """Resolve a profile by name (None/empty = the team-codebook default)."""
    key = name or DEFAULT_PROFILE
    profile = PROFILES.get(key)
    if profile is None:
        raise UnknownProfileError(f"unknown release profile {name!r} (known: {sorted(PROFILES)})")
    return profile


def _profile_to_config(profile: ResolvedProfile) -> dict[str, object]:
    """YAML-shaped view of one profile (None thresholds omitted, not null)."""
    payload: dict[str, object] = {
        "required_gates": list(profile.required_gates),
        "alpha_tentative": profile.alpha_tentative,
        "alpha_reliable": profile.alpha_reliable,
        "min_audit_sample": profile.min_audit_sample,
        "min_stratum_size": profile.min_stratum_size,
        "partition_rule": profile.partition_rule,
    }
    optional: dict[str, object] = {
        "per_code_f1_floor": profile.per_code_f1_floor,
        "content_stability_floor": profile.content_stability_floor,
        "prompt_stability_alpha": profile.prompt_stability_alpha,
        "alt_test_margin": profile.alt_test_margin,
        "alt_test_passing_fraction": profile.alt_test_passing_fraction,
        "max_stratum_disparity": profile.max_stratum_disparity,
        "agreement_routing_threshold": profile.agreement_routing_threshold,
    }
    payload.update({k: v for k, v in optional.items() if v is not None})
    return payload


def release_config_defaults() -> dict[str, object]:
    """The ``research.release.*`` config block (v6 §6) for BOTH yaml locations."""
    return {
        "release": {
            "default_profile": DEFAULT_PROFILE,
            "enforcement": ENFORCEMENT_WARN,
            "profiles": {name: _profile_to_config(profile) for name, profile in PROFILES.items()},
        }
    }


RELEASE_CONFIG_DEFAULTS: dict[str, object] = release_config_defaults()
DEFAULTS: dict[str, object] = RELEASE_CONFIG_DEFAULTS
