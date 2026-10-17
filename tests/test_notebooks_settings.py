"""UX Wave 6 notebooks settings: the typed reader + the config three-ways.

Mirrors tests/test_ui_settings.py: the defaults lockstep
(get_default_config == both shipped YAML files), the load-time gates on
``notebooks.enabled``/``notebooks.directory``, and the validate_configs
presence+type checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from corpus_kb._setup.validate_configs import RUNTIME_CONFIG_KEYS, validate_runtime_config
from corpus_kb.config import get_default_config, load_config
from corpus_kb.notebooks.settings import (
    NOTEBOOKS_CONFIG_DEFAULTS,
    load_notebooks_settings,
    notebooks_settings_error,
)

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Defaults lockstep (the three-ways rule)
# ---------------------------------------------------------------------------


def _yaml_notebooks(name: str) -> dict[str, object]:
    raw: dict[str, object] = yaml.safe_load((REPO / name).read_text(encoding="utf-8"))
    return raw["notebooks"]  # type-checked by the assert below


def test_notebooks_defaults_are_in_lockstep_across_three_ways() -> None:
    defaults = get_default_config()["notebooks"]
    assert defaults == NOTEBOOKS_CONFIG_DEFAULTS
    assert _yaml_notebooks("config.yaml") == defaults
    assert _yaml_notebooks("src/corpus_kb/config.yaml") == defaults


def test_load_config_carries_the_notebooks_block() -> None:
    cfg = load_config()
    assert cfg["notebooks"]["enabled"] is True
    assert cfg["notebooks"]["directory"] == "notebooks"


def test_notebooks_defaults_are_not_aliased_across_loads() -> None:
    """Mutating one loaded config must not leak into the next load."""
    first = load_config()
    notebooks = first["notebooks"]
    assert isinstance(notebooks, dict)
    notebooks["enabled"] = False
    second = load_config()
    assert second["notebooks"]["enabled"] is True


def test_load_notebooks_settings_reads_a_partial_block() -> None:
    settings = load_notebooks_settings({})
    assert settings.enabled is True
    assert settings.directory == "notebooks"
    settings = load_notebooks_settings({"notebooks": {"enabled": False}})
    assert settings.enabled is False


# ---------------------------------------------------------------------------
# The load-time gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["yes", 1, None])
def test_non_bool_enabled_is_rejected(bad: object) -> None:
    assert notebooks_settings_error({"notebooks": {"enabled": bad}}) is not None


@pytest.mark.parametrize("bad", ["", "   ", 7, None])
def test_non_string_directory_is_rejected(bad: object) -> None:
    assert notebooks_settings_error({"notebooks": {"directory": bad}}) is not None


def test_valid_block_has_no_error() -> None:
    assert notebooks_settings_error({"notebooks": {"enabled": False}}) is None
    assert notebooks_settings_error({"notebooks": {"directory": "/tmp/nb"}}) is None
    assert notebooks_settings_error({}) is None


def test_load_config_rejects_non_bool_enabled(tmp_path: Path) -> None:
    """The bad flag fails AT LOAD with the remediation text."""
    base = (REPO / "config.yaml").read_text(encoding="utf-8")
    assert base.count("notebooks:\n  enabled: true") == 1  # only the block pins it
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(base.replace("notebooks:\n  enabled: true", 'notebooks:\n  enabled: "yes"'))
    with pytest.raises(ValueError, match=r"notebooks\.enabled"):
        load_config(str(cfg_file))


def test_load_config_rejects_empty_directory(tmp_path: Path) -> None:
    base = (REPO / "config.yaml").read_text(encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(base.replace("directory: notebooks", 'directory: ""'))
    with pytest.raises(ValueError, match=r"notebooks\.directory"):
        load_config(str(cfg_file))


# ---------------------------------------------------------------------------
# validate_configs presence+type checks
# ---------------------------------------------------------------------------


def test_runtime_config_keys_pin_the_notebooks_keys() -> None:
    assert RUNTIME_CONFIG_KEYS["notebooks.enabled"] is bool
    assert RUNTIME_CONFIG_KEYS["notebooks.directory"] is str


def _shipped_yaml() -> dict[str, object]:
    """The root config.yaml (what validate_configs runs against on disk)."""
    raw: dict[str, object] = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    return raw


def test_validate_runtime_config_accepts_the_shipped_block() -> None:
    assert validate_runtime_config(_shipped_yaml()) == []


def test_validate_runtime_config_rejects_bad_notebooks_values() -> None:
    cfg = _shipped_yaml()
    notebooks = cfg["notebooks"]
    assert isinstance(notebooks, dict)
    notebooks["enabled"] = "true"
    notebooks["directory"] = 3
    errors = validate_runtime_config(cfg)
    assert any("notebooks.enabled" in error for error in errors)
    assert any("notebooks.directory" in error for error in errors)
