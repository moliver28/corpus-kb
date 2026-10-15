"""Wave 4 topics settings: the typed reader + the config three-ways.

Covers:

* the defaults lockstep (get_default_config == both shipped YAML files);
* the load-time method gate (only "bertopic"; unknown is a ValueError at
  load_config, never a silent ignore at build time);
* the ``min_topic_size`` union rule ("auto" or a positive int; bools and
  non-positives are rejected — bool is an int subclass);
* ``resolved_min_topic_size`` ("auto" resolves to the BERTopic default
  and is reported; an int passes through);
* the validate_configs presence+type checks for the ``topics`` keys.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corpus_kb._setup.validate_configs import RUNTIME_CONFIG_KEYS, validate_runtime_config
from corpus_kb.config import get_default_config, load_config
from corpus_kb.topics.settings import (
    AUTO_MIN_TOPIC_SIZE,
    TOPICS_CONFIG_DEFAULTS,
    UMAP_RANDOM_STATE,
    load_topics_settings,
    topics_settings_error,
)

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Defaults lockstep (the three-ways rule)
# ---------------------------------------------------------------------------


def _yaml_topics(name: str) -> dict[str, object]:
    raw: dict[str, object] = yaml.safe_load((REPO / name).read_text(encoding="utf-8"))
    return raw["topics"]  # type-checked by the assert below


def test_topics_defaults_are_in_lockstep_across_three_ways() -> None:
    defaults = get_default_config()["topics"]
    assert defaults == TOPICS_CONFIG_DEFAULTS
    assert _yaml_topics("config.yaml") == defaults
    assert _yaml_topics("src/corpus_kb/config.yaml") == defaults


def test_load_config_carries_the_topics_block() -> None:
    cfg = load_config()
    assert cfg["topics"]["enabled"] is False
    assert cfg["topics"]["method"] == "bertopic"
    assert cfg["topics"]["llm_reduction"] is True
    assert cfg["topics"]["min_topic_size"] == "auto"


def test_topics_defaults_are_not_aliased_across_loads() -> None:
    """Mutating one loaded config must not leak into the next load."""
    first = load_config()
    topics = first["topics"]
    assert isinstance(topics, dict)
    topics["enabled"] = True
    second = load_config()
    assert second["topics"]["enabled"] is False


def test_determinism_pin_is_a_code_constant() -> None:
    assert UMAP_RANDOM_STATE == 42
    assert AUTO_MIN_TOPIC_SIZE == 10


# ---------------------------------------------------------------------------
# The method gate (load-time rejection with remediation)
# ---------------------------------------------------------------------------


def _config_with_topics_block(block: dict[str, object]) -> dict[str, object]:
    cfg = get_default_config()
    cfg["topics"] = {**TOPICS_CONFIG_DEFAULTS, **block}
    return cfg


@pytest.mark.parametrize("method", ["lda", "top2vec", "BERTOPIC", ""])
def test_unknown_method_is_rejected_at_load(method: str) -> None:
    cfg = _config_with_topics_block({"method": method})
    error = topics_settings_error(cfg)
    assert error is not None
    assert "bertopic" in error
    with pytest.raises(ValueError, match="bertopic"):
        load_topics_settings(cfg)


def test_load_config_raises_on_unknown_method(tmp_path: Path) -> None:
    """The unknown method fails AT LOAD with the remediation text."""
    base = (REPO / "config.yaml").read_text(encoding="utf-8")
    assert base.count("method: bertopic") == 1  # only the topics block pins it
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(base.replace("method: bertopic", "method: lda"), encoding="utf-8")
    with pytest.raises(ValueError, match="topics.method"):
        load_config(str(cfg_file))


def test_load_config_raises_on_bad_min_topic_size(tmp_path: Path) -> None:
    base = (REPO / "config.yaml").read_text(encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        base.replace("min_topic_size: auto", "min_topic_size: 0"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="topics.min_topic_size"):
        load_config(str(cfg_file))


def test_partial_block_fills_defaults() -> None:
    settings = load_topics_settings({"topics": {"enabled": True}})
    assert settings.enabled is True
    assert settings.method == "bertopic"
    assert settings.llm_reduction is True
    assert settings.min_topic_size == "auto"
    assert settings.live_fallback is False


# ---------------------------------------------------------------------------
# min_topic_size: the "auto" | positive-int union
# ---------------------------------------------------------------------------


def test_min_topic_size_auto_resolves_to_bertopic_default() -> None:
    settings = load_topics_settings({})
    assert settings.min_topic_size == "auto"
    assert settings.resolved_min_topic_size() == AUTO_MIN_TOPIC_SIZE


@pytest.mark.parametrize("size", [3, 25, 1])
def test_min_topic_size_int_passes_through(size: int) -> None:
    settings = load_topics_settings({"topics": {"min_topic_size": size}})
    assert settings.min_topic_size == size
    assert settings.resolved_min_topic_size() == size


@pytest.mark.parametrize("size", [0, -5])
def test_min_topic_size_non_positive_is_rejected(size: int) -> None:
    assert topics_settings_error({"topics": {"min_topic_size": size}}) is not None


def test_min_topic_size_bool_is_rejected() -> None:
    """bool is an int subclass: `min_topic_size: true` must not slip through."""
    assert topics_settings_error({"topics": {"min_topic_size": True}}) is not None


def test_min_topic_size_unknown_word_is_rejected() -> None:
    assert topics_settings_error({"topics": {"min_topic_size": "ten"}}) is not None


def test_llm_keys_must_be_non_empty_strings() -> None:
    assert topics_settings_error({"topics": {"llm": ""}}) is not None
    assert topics_settings_error({"topics": {"llm_base_url": 42}}) is not None


def test_valid_block_has_no_error() -> None:
    assert (
        topics_settings_error(
            {"topics": {"enabled": True, "min_topic_size": 12, "method": "bertopic"}}
        )
        is None
    )


# ---------------------------------------------------------------------------
# validate_configs presence+type checks
# ---------------------------------------------------------------------------


def test_runtime_config_keys_pin_the_topics_keys() -> None:
    assert RUNTIME_CONFIG_KEYS["topics.enabled"] is bool
    assert RUNTIME_CONFIG_KEYS["topics.method"] is str
    assert RUNTIME_CONFIG_KEYS["topics.llm_reduction"] is bool
    assert RUNTIME_CONFIG_KEYS["topics.llm"] is str
    assert RUNTIME_CONFIG_KEYS["topics.llm_base_url"] is str
    # The union type lives in the cross-field rules, not the single-type map.
    assert "topics.min_topic_size" not in RUNTIME_CONFIG_KEYS


def _shipped_yaml_topics() -> dict[str, object]:
    """The root config.yaml (what validate_configs runs against on disk)."""
    raw: dict[str, object] = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    return raw


def test_validate_runtime_config_accepts_the_shipped_block() -> None:
    assert validate_runtime_config(_shipped_yaml_topics()) == []


def test_validate_runtime_config_rejects_missing_min_topic_size() -> None:
    cfg = _shipped_yaml_topics()
    topics = cfg["topics"]
    assert isinstance(topics, dict)
    del topics["min_topic_size"]
    errors = validate_runtime_config(cfg)
    assert any("topics.min_topic_size" in error for error in errors)


def test_validate_runtime_config_rejects_bad_min_topic_size() -> None:
    def _with(size: object) -> dict[str, object]:
        cfg = _shipped_yaml_topics()
        topics = cfg["topics"]
        assert isinstance(topics, dict)
        topics["min_topic_size"] = size
        return cfg

    assert validate_runtime_config(_with(0)) != []
    assert validate_runtime_config(_with(True)) != []
    assert validate_runtime_config(_with("ten")) != []
    assert validate_runtime_config(_with(30)) == []


def test_validate_runtime_config_rejects_unknown_method() -> None:
    errors = validate_runtime_config(_config_with_topics_block({"method": "lda"}))
    assert any("topics.method" in error for error in errors)
