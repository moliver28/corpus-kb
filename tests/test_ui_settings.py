"""UX Wave 3 UI settings: the typed reader + the config three-ways.

Mirrors tests/test_topics_settings.py: the defaults lockstep
(get_default_config == both shipped YAML files), the load-time bool gate
on ``ui.enabled``, and the validate_configs presence+type check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corpus_kb._setup.validate_configs import RUNTIME_CONFIG_KEYS, validate_runtime_config
from corpus_kb.config import get_default_config, load_config
from corpus_kb.ui.settings import UI_CONFIG_DEFAULTS, load_ui_settings, ui_settings_error

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Defaults lockstep (the three-ways rule)
# ---------------------------------------------------------------------------


def _yaml_ui(name: str) -> dict[str, object]:
    raw: dict[str, object] = yaml.safe_load((REPO / name).read_text(encoding="utf-8"))
    return raw["ui"]  # type-checked by the assert below


def test_ui_defaults_are_in_lockstep_across_three_ways() -> None:
    defaults = get_default_config()["ui"]
    assert defaults == UI_CONFIG_DEFAULTS
    assert _yaml_ui("config.yaml") == defaults
    assert _yaml_ui("src/corpus_kb/config.yaml") == defaults


def test_load_config_carries_the_ui_block() -> None:
    cfg = load_config()
    assert cfg["ui"]["enabled"] is True


def test_ui_defaults_are_not_aliased_across_loads() -> None:
    """Mutating one loaded config must not leak into the next load."""
    first = load_config()
    ui = first["ui"]
    assert isinstance(ui, dict)
    ui["enabled"] = False
    second = load_config()
    assert second["ui"]["enabled"] is True


def test_load_ui_settings_reads_a_partial_block() -> None:
    settings = load_ui_settings({})
    assert settings.enabled is True
    settings = load_ui_settings({"ui": {"enabled": False}})
    assert settings.enabled is False


# ---------------------------------------------------------------------------
# The load-time bool gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["yes", 1, None])
def test_non_bool_enabled_is_rejected(bad: object) -> None:
    assert ui_settings_error({"ui": {"enabled": bad}}) is not None


def test_valid_block_has_no_error() -> None:
    assert ui_settings_error({"ui": {"enabled": False}}) is None
    assert ui_settings_error({}) is None


def test_load_config_rejects_non_bool_enabled(tmp_path: Path) -> None:
    """The bad flag fails AT LOAD with the remediation text."""
    base = (REPO / "config.yaml").read_text(encoding="utf-8")
    assert base.count("ui:\n  enabled: true") == 1  # only the ui block pins it
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(base.replace("ui:\n  enabled: true", 'ui:\n  enabled: "yes"'))
    with pytest.raises(ValueError, match=r"ui\.enabled"):
        load_config(str(cfg_file))


# ---------------------------------------------------------------------------
# validate_configs presence+type checks
# ---------------------------------------------------------------------------


def test_runtime_config_keys_pin_the_ui_keys() -> None:
    assert RUNTIME_CONFIG_KEYS["ui.enabled"] is bool


def _shipped_yaml() -> dict[str, object]:
    """The root config.yaml (what validate_configs runs against on disk)."""
    raw: dict[str, object] = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    return raw


def test_validate_runtime_config_accepts_the_shipped_block() -> None:
    assert validate_runtime_config(_shipped_yaml()) == []


def test_validate_runtime_config_rejects_missing_ui_enabled() -> None:
    cfg = _shipped_yaml()
    ui = cfg["ui"]
    assert isinstance(ui, dict)
    del ui["enabled"]
    errors = validate_runtime_config(cfg)
    assert any("ui.enabled" in error for error in errors)


def test_validate_runtime_config_rejects_bad_ui_enabled() -> None:
    cfg = _shipped_yaml()
    ui = cfg["ui"]
    assert isinstance(ui, dict)
    ui["enabled"] = "true"
    errors = validate_runtime_config(cfg)
    assert any("ui.enabled" in error for error in errors)
