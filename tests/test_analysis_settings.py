"""Analysis program settings: the typed reader + the config merge.

Covers:

* the defaults merge (get_default_config == the typed module's defaults;
  every feature flag OFF);
* the load-time gates (only tier ``head``/``setfit``; only ``undated``
  ``drop``/``report``; ``bins``/``min_signals``/``min_labels_per_class``
  positive ints or "auto" — unknown values are a ValueError at
  load_config, never a silent ignore at build time);
* ``resolved_bins`` ("auto" resolves to the reported default; an int
  passes through);
* the no-aliasing rule (mutating one loaded config never leaks into the
  next load).

NOTE on the lockstep scope: the ``analysis.*`` keys are deliberately
ABSENT from both shipped YAML files in Wave 0 (a concurrent program owns
config-truth lockstep there); absent keys fill from the typed defaults
through ``config._apply_typed_module_defaults`` — that is the merge
mechanism this module exists for. So the topics suite's "three ways"
YAML assertions do not apply here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_kb.analysis.settings import (
    ANALYSIS_CONFIG_DEFAULTS,
    AUTO_OVER_TIME_BINS,
    analysis_settings_error,
    load_analysis_settings,
)
from corpus_kb.config import get_default_config, load_config

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Defaults merge (the typed-defaults rule)
# ---------------------------------------------------------------------------


def test_analysis_defaults_merge_into_default_config() -> None:
    defaults = get_default_config()["analysis"]
    assert defaults == ANALYSIS_CONFIG_DEFAULTS


def test_every_analysis_feature_defaults_to_off() -> None:
    cfg = load_config()
    assert cfg["analysis"]["labeling"]["enabled"] is False
    assert cfg["analysis"]["classification"]["enabled"] is False
    assert cfg["analysis"]["topics"]["over_time"]["enabled"] is False


def test_load_analysis_settings_defaults_are_off() -> None:
    settings = load_analysis_settings({})
    assert settings.labeling.enabled is False
    assert settings.classification.enabled is False
    assert settings.classification.tier == "head"
    assert settings.over_time.enabled is False
    assert settings.over_time.bins == "auto"
    assert settings.over_time.undated == "drop"


def test_auto_bins_is_a_code_constant() -> None:
    assert AUTO_OVER_TIME_BINS == 10


def test_resolved_bins_resolves_auto_and_passes_int_through() -> None:
    auto = load_analysis_settings({})
    assert auto.over_time.resolved_bins() == AUTO_OVER_TIME_BINS
    pinned = load_analysis_settings({"analysis": {"topics": {"over_time": {"bins": 24}}}})
    assert pinned.over_time.resolved_bins() == 24


def test_partial_block_fills_defaults() -> None:
    settings = load_analysis_settings({"analysis": {"labeling": {"enabled": True}}})
    assert settings.labeling.enabled is True
    assert settings.labeling.min_signals == 1
    assert settings.classification.enabled is False
    assert settings.over_time.undated == "drop"


def test_defaults_are_not_aliased_across_loads() -> None:
    """Mutating one loaded config must not leak into the next load."""
    first = load_config()
    analysis = first["analysis"]
    assert isinstance(analysis, dict)
    labeling = analysis["labeling"]
    assert isinstance(labeling, dict)
    labeling["enabled"] = True
    second = load_config()
    assert second["analysis"]["labeling"]["enabled"] is False


# ---------------------------------------------------------------------------
# The load-time gates (rejection with remediation)
# ---------------------------------------------------------------------------


def _config_with(block: dict[str, object]) -> dict[str, object]:
    return {"analysis": block}


@pytest.mark.parametrize("tier", ["gpt", "HEAD", "bert", ""])
def test_unknown_tier_is_rejected_at_load(tier: str) -> None:
    cfg = _config_with({"classification": {"tier": tier}})
    error = analysis_settings_error(cfg)
    assert error is not None
    assert "analysis.classification.tier" in error
    with pytest.raises(ValueError, match=r"analysis\.classification\.tier"):
        load_analysis_settings(cfg)


@pytest.mark.parametrize("undated", ["ignore", "nan", "DROP", ""])
def test_unknown_undated_policy_is_rejected_at_load(undated: str) -> None:
    cfg = _config_with({"topics": {"over_time": {"undated": undated}}})
    error = analysis_settings_error(cfg)
    assert error is not None
    assert "analysis.topics.over_time.undated" in error
    with pytest.raises(ValueError, match=r"analysis\.topics\.over_time\.undated"):
        load_analysis_settings(cfg)


@pytest.mark.parametrize("bins", [True, 0, -1, "ten", "AUTO"])
def test_bad_bins_is_rejected_at_load(bins: object) -> None:
    cfg = _config_with({"topics": {"over_time": {"bins": bins}}})
    error = analysis_settings_error(cfg)
    assert error is not None
    assert "analysis.topics.over_time.bins" in error
    with pytest.raises(ValueError, match=r"analysis\.topics\.over_time\.bins"):
        load_analysis_settings(cfg)


@pytest.mark.parametrize(
    ("dotted", "value"),
    [
        ("analysis.labeling.min_signals", True),
        ("analysis.labeling.min_signals", 0),
        ("analysis.classification.min_labels_per_class", True),
        ("analysis.classification.min_labels_per_class", -3),
    ],
)
def test_bad_positive_ints_are_rejected_at_load(dotted: str, value: object) -> None:
    section, key = dotted.split(".")[1], dotted.split(".")[2]
    cfg = _config_with({section: {key: value}})
    error = analysis_settings_error(cfg)
    assert error is not None
    assert dotted in error
    with pytest.raises(ValueError, match=dotted.replace(".", r"\.")):
        load_analysis_settings(cfg)


def test_load_config_raises_on_invalid_block_from_file(tmp_path: Path) -> None:
    """A bad tier in a user YAML fails AT LOAD with the remediation text."""
    base = (REPO / "config.yaml").read_text(encoding="utf-8")
    assert "analysis:" not in base  # Wave 0: the shipped YAML carries no block
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        base + "\nanalysis:\n  classification:\n    enabled: true\n    tier: gpt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"analysis.classification.tier"):
        load_config(str(cfg_file))
