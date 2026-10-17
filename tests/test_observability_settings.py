"""UX program Wave 4: the ``observability.*`` config block (three-ways rule).

Mirrors tests/test_topics_settings.py: the defaults lockstep
(``get_default_config`` == both shipped YAML files), the load-time env
override, and the validate_configs presence+type checks. The tracing
reader's constants (``utils/tracing.py`` DEFAULT_*) must match the block.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml

from corpus_kb._setup.validate_configs import RUNTIME_CONFIG_KEYS, validate_runtime_config
from corpus_kb.config import KNOWN_CORPUS_KB_ENV_VARS, get_default_config, load_config
from corpus_kb.utils.tracing import DEFAULT_OTLP_ENDPOINT, DEFAULT_SERVICE_NAME

REPO = Path(__file__).resolve().parent.parent

OBSERVABILITY_DEFAULTS: dict[str, object] = {
    "enabled": False,
    "otlp_endpoint": "http://localhost:4318",
    "service_name": "corpus-kb",
}


def _yaml_observability(name: str) -> dict[str, object]:
    raw: dict[str, object] = yaml.safe_load((REPO / name).read_text(encoding="utf-8"))
    return cast("dict[str, object]", raw["observability"])


def _shipped_yaml() -> dict[str, object]:
    raw: dict[str, object] = yaml.safe_load((REPO / "config.yaml").read_text(encoding="utf-8"))
    return raw


# ---------------------------------------------------------------------------
# Defaults lockstep (the three-ways rule)
# ---------------------------------------------------------------------------


def test_observability_defaults_are_in_lockstep_across_three_ways() -> None:
    assert get_default_config()["observability"] == OBSERVABILITY_DEFAULTS
    assert _yaml_observability("config.yaml") == OBSERVABILITY_DEFAULTS
    assert _yaml_observability("src/corpus_kb/config.yaml") == OBSERVABILITY_DEFAULTS


def test_tracing_reader_defaults_match_the_config_block() -> None:
    assert OBSERVABILITY_DEFAULTS["otlp_endpoint"] == DEFAULT_OTLP_ENDPOINT
    assert OBSERVABILITY_DEFAULTS["service_name"] == DEFAULT_SERVICE_NAME


def test_load_config_carries_the_block() -> None:
    cfg = load_config()
    block = cast("dict[str, object]", cfg["observability"])
    assert block["enabled"] is False
    assert block["otlp_endpoint"] == "http://localhost:4318"
    assert block["service_name"] == "corpus-kb"


def test_observability_defaults_are_not_aliased_across_loads() -> None:
    """Mutating one loaded config must not leak into the next load."""
    first = load_config()
    observability = first["observability"]
    assert isinstance(observability, dict)
    observability["enabled"] = True
    second = load_config()
    assert cast("dict[str, object]", second["observability"])["enabled"] is False


# ---------------------------------------------------------------------------
# The env override
# ---------------------------------------------------------------------------


def test_otlp_endpoint_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORPUS_KB_OTLP_ENDPOINT", "http://collector:4318")
    cfg = load_config()
    block = cast("dict[str, object]", cfg["observability"])
    assert block["otlp_endpoint"] == "http://collector:4318"


def test_corpus_kb_otlp_endpoint_is_a_known_env_var() -> None:
    assert "CORPUS_KB_OTLP_ENDPOINT" in KNOWN_CORPUS_KB_ENV_VARS


# ---------------------------------------------------------------------------
# validate_configs presence+type checks
# ---------------------------------------------------------------------------


def test_runtime_config_keys_pin_the_observability_keys() -> None:
    assert RUNTIME_CONFIG_KEYS["observability.enabled"] is bool
    assert RUNTIME_CONFIG_KEYS["observability.otlp_endpoint"] is str
    assert RUNTIME_CONFIG_KEYS["observability.service_name"] is str


def test_validate_runtime_config_accepts_the_shipped_block() -> None:
    assert validate_runtime_config(_shipped_yaml()) == []


def test_validate_runtime_config_rejects_bad_types() -> None:
    cfg = _shipped_yaml()
    observability = cast("dict[str, object]", cfg["observability"])
    observability["enabled"] = "yes"
    assert any("observability.enabled" in error for error in validate_runtime_config(cfg))
    observability["enabled"] = False
    observability["otlp_endpoint"] = 4318
    assert any("observability.otlp_endpoint" in error for error in validate_runtime_config(cfg))


def test_validate_runtime_config_rejects_missing_block() -> None:
    cfg = _shipped_yaml()
    del cfg["observability"]
    errors = validate_runtime_config(cfg)
    assert any("observability.enabled" in error for error in errors)
    assert any("observability.otlp_endpoint" in error for error in errors)
    assert any("observability.service_name" in error for error in errors)
