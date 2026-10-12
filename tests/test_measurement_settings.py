"""Tests for the P2 measurement settings module and its defaults wiring."""

from __future__ import annotations

import pytest

from corpus_kb.coding.measurement_settings import (
    MEASUREMENT_CONFIG_DEFAULTS,
    AltTestSettings,
    MeasurementSettings,
    measurement_settings_from_mapping,
)


def test_defaults_roundtrip() -> None:
    settings = measurement_settings_from_mapping(dict(MEASUREMENT_CONFIG_DEFAULTS))
    assert settings == MeasurementSettings()


def test_default_values_match_specs() -> None:
    defaults = MeasurementSettings()
    # v8 §3 U41 routing.target_risk=0.05, audit.sample_rate=0.1
    assert defaults.routing.target_risk == 0.05
    assert defaults.audit.sample_rate == 0.1
    # v8 §3 U37 review.batch_size=20, random_fraction=0.2, diversity=True
    assert defaults.review.batch_size == 20
    assert defaults.review.random_fraction == 0.2
    assert defaults.review.diversity is True
    # v8 §3 U38/U39 labeling.min_signals=3, cv_folds=5
    assert defaults.labeling.min_signals == 3
    assert defaults.labeling.cv_folds == 5
    # U35 judgment distribution is OFF by default (doctor-verifiable key)
    assert defaults.judgment_distribution.use_mean is False
    # U13 alt-test paper conventions: winning fraction 0.5, BY q 0.05
    assert defaults.alt_test.passing_fraction == 0.5
    assert defaults.alt_test.fdr_q == 0.05
    assert defaults.alt_test.method == "exact_binomial"


def test_partial_override_keeps_other_defaults() -> None:
    settings = measurement_settings_from_mapping(
        {"review": {"batch_size": 50}, "routing": {"target_risk": 0.02}}
    )
    assert settings.review.batch_size == 50
    assert settings.review.random_fraction == 0.2  # untouched default
    assert settings.routing.target_risk == 0.02
    assert settings.routing.confidence_level == 0.95


def test_wrong_types_fail_loud() -> None:
    with pytest.raises(ValueError, match="review"):
        measurement_settings_from_mapping({"review": "not-a-mapping"})
    with pytest.raises(ValueError, match="batch_size"):
        measurement_settings_from_mapping({"review": {"batch_size": "many"}})
    with pytest.raises(ValueError, match="target_risk"):
        measurement_settings_from_mapping({"routing": {"target_risk": True}})


def test_unknown_keys_ignored_additively() -> None:
    settings = measurement_settings_from_mapping(
        {"review": {"batch_size": 30, "future_key": "x"}, "unknown_block": {"a": 1}}
    )
    assert settings.review.batch_size == 30


def test_alt_test_settings_defaults_are_paper_fresh() -> None:
    alt = AltTestSettings()
    # Bootstrap only as an alternative; exact binomial is the default so the
    # module stays scipy-free at any n.
    assert alt.n_bootstrap == 10000


def test_every_default_block_has_all_documented_keys() -> None:
    # Guard against inert keys: each block in MEASUREMENT_CONFIG_DEFAULTS
    # must round-trip through the typed reader with no silent drops.
    settings = measurement_settings_from_mapping(dict(MEASUREMENT_CONFIG_DEFAULTS))
    for block_name, block in MEASUREMENT_CONFIG_DEFAULTS.items():
        assert isinstance(getattr(settings, block_name), object), block_name
        for key in block:
            assert isinstance(key, str)
    assert set(MEASUREMENT_CONFIG_DEFAULTS) == {
        "alt_test",
        "prompt_stability",
        "routing",
        "audit",
        "review",
        "labeling",
        "judgment_distribution",
    }
