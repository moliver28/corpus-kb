"""Typed settings + defaults for the P2 measurement-math modules.

Every key here is read by a real function in this phase (no inert config):
the orchestrator wires ``MEASUREMENT_CONFIG_DEFAULTS`` into both YAML config
locations; the dataclasses are the typed readers the modules take. Defaults
live ONLY here so YAML and code cannot drift.

Sources: v6 §7 (U12-U19, U33-U36), v8 §3 (U37-U39, U41, U47).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AltTestSettings:
    """U13 Alternative Annotator Test (Calderon et al. 2025) parameters.

    ``margin`` is the cost-benefit penalty epsilon; the paper's guidance is
    0.1 for crowd-workers, 0.15 for skilled annotators, 0.2 for experts.
    """

    margin: float = 0.1
    fdr_q: float = 0.05
    passing_fraction: float = 0.5
    method: str = "exact_binomial"  # exact_binomial | bootstrap
    n_bootstrap: int = 10000


@dataclass(frozen=True)
class PromptStabilitySettings:
    """U12 prompt-variant agreement gate threshold (krippendorff alpha)."""

    alpha_threshold: float = 0.667
    min_variants: int = 2


@dataclass(frozen=True)
class RoutingSettings:
    """U41 risk-coverage routing config (v8 §3 U41 ``routing.*``)."""

    target_risk: float = 0.05
    confidence_level: float = 0.95
    min_calibration_n: int = 30


@dataclass(frozen=True)
class AuditSettings:
    """U17 audit sampling config (v8 §3 U41 ``audit.sample_rate``)."""

    sample_rate: float = 0.1
    seed: int = 42


@dataclass(frozen=True)
class ReviewSettings:
    """U37 active review batching config (v8 §3 U37 ``review.*``)."""

    batch_size: int = 20
    random_fraction: float = 0.2
    diversity: bool = True


@dataclass(frozen=True)
class LabelingSettings:
    """U38/U39 weak-supervision + label-triage config (``labeling.*``)."""

    aggregation: str = "em"  # vote | em
    min_signals: int = 3
    cv_folds: int = 5
    flag_threshold: float = 0.2


@dataclass(frozen=True)
class JudgmentDistributionSettings:
    """U35 judgment-distribution mean behind an explicit config key.

    Off by default: mean-over-sampled-outputs requires a runtime that
    exposes token probabilities; doctor verifies that prerequisite.
    """

    use_mean: bool = False


@dataclass(frozen=True)
class MeasurementSettings:
    """Aggregate typed handle over every P2 measurement block."""

    alt_test: AltTestSettings = field(default_factory=AltTestSettings)
    prompt_stability: PromptStabilitySettings = field(default_factory=PromptStabilitySettings)
    routing: RoutingSettings = field(default_factory=RoutingSettings)
    audit: AuditSettings = field(default_factory=AuditSettings)
    review: ReviewSettings = field(default_factory=ReviewSettings)
    labeling: LabelingSettings = field(default_factory=LabelingSettings)
    judgment_distribution: JudgmentDistributionSettings = field(
        default_factory=JudgmentDistributionSettings
    )


def _require_block(raw: dict[str, object], key: str) -> dict[str, object]:
    """Fetch a nested mapping, failing loud on a non-mapping YAML block."""
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"measurement.{key} must be a mapping, got {type(value).__name__}")
    return value


def _as_float(block: dict[str, object], key: str, default: float) -> float:
    """Typed numeric getter: rejects bools and non-numbers with a clear error."""
    value = block.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"measurement.{key} must be a number, got {type(value).__name__}")
    return float(value)


def _as_int(block: dict[str, object], key: str, default: int) -> int:
    value = block.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"measurement.{key} must be an integer, got {type(value).__name__}")
    return value


def _as_bool(block: dict[str, object], key: str, default: bool) -> bool:
    value = block.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    raise ValueError(f"measurement.{key} must be a boolean, got {type(value).__name__}")


def measurement_settings_from_mapping(raw: dict[str, object]) -> MeasurementSettings:
    """Build typed settings from a flat YAML-ish mapping (one block per key).

    Unknown keys are ignored (additive-forward); wrong types raise ValueError
    so misconfiguration fails loud instead of silently using defaults.
    """
    alt = _require_block(raw, "alt_test")
    prompt = _require_block(raw, "prompt_stability")
    routing = _require_block(raw, "routing")
    audit = _require_block(raw, "audit")
    review = _require_block(raw, "review")
    labeling = _require_block(raw, "labeling")
    jd = _require_block(raw, "judgment_distribution")
    return MeasurementSettings(
        alt_test=AltTestSettings(
            margin=_as_float(alt, "margin", 0.1),
            fdr_q=_as_float(alt, "fdr_q", 0.05),
            passing_fraction=_as_float(alt, "passing_fraction", 0.5),
            method=str(alt.get("method", "exact_binomial")),
            n_bootstrap=_as_int(alt, "n_bootstrap", 10000),
        ),
        prompt_stability=PromptStabilitySettings(
            alpha_threshold=_as_float(prompt, "alpha_threshold", 0.667),
            min_variants=_as_int(prompt, "min_variants", 2),
        ),
        routing=RoutingSettings(
            target_risk=_as_float(routing, "target_risk", 0.05),
            confidence_level=_as_float(routing, "confidence_level", 0.95),
            min_calibration_n=_as_int(routing, "min_calibration_n", 30),
        ),
        audit=AuditSettings(
            sample_rate=_as_float(audit, "sample_rate", 0.1),
            seed=_as_int(audit, "seed", 42),
        ),
        review=ReviewSettings(
            batch_size=_as_int(review, "batch_size", 20),
            random_fraction=_as_float(review, "random_fraction", 0.2),
            diversity=_as_bool(review, "diversity", True),
        ),
        labeling=LabelingSettings(
            aggregation=str(labeling.get("aggregation", "em")),
            min_signals=_as_int(labeling, "min_signals", 3),
            cv_folds=_as_int(labeling, "cv_folds", 5),
            flag_threshold=_as_float(labeling, "flag_threshold", 0.2),
        ),
        judgment_distribution=JudgmentDistributionSettings(
            use_mean=_as_bool(jd, "use_mean", False),
        ),
    )


MEASUREMENT_CONFIG_DEFAULTS: dict[str, dict[str, object]] = {
    "alt_test": {
        "margin": 0.1,
        "fdr_q": 0.05,
        "passing_fraction": 0.5,
        "method": "exact_binomial",
        "n_bootstrap": 10000,
    },
    "prompt_stability": {
        "alpha_threshold": 0.667,
        "min_variants": 2,
    },
    "routing": {
        "target_risk": 0.05,
        "confidence_level": 0.95,
        "min_calibration_n": 30,
    },
    "audit": {
        "sample_rate": 0.1,
        "seed": 42,
    },
    "review": {
        "batch_size": 20,
        "random_fraction": 0.2,
        "diversity": True,
    },
    "labeling": {
        "aggregation": "em",
        "min_signals": 3,
        "cv_folds": 5,
        "flag_threshold": 0.2,
    },
    "judgment_distribution": {
        "use_mean": False,
    },
}
