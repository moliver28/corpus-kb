"""Model tiers (TD-04): the coding.tier selector, defaults, and overrides."""

from __future__ import annotations

from typing import Any, cast

import pytest

from corpus_kb.coding.model_tiers import (
    MODEL_TIERS_DEFAULTS,
    default_coder_models,
    resolve_coder_models,
    resolve_tier,
)
from corpus_kb.errors import ConfigError


def _config(**coding_overrides: Any) -> dict[str, Any]:
    return {
        "models": {"tiers": MODEL_TIERS_DEFAULTS},
        "coding": {"tier": "medium", **coding_overrides},
    }


def test_tier_resolution_matches_snapshot() -> None:
    config = _config()
    assert resolve_tier("small", config)["model"] == "qwen3:4b"
    assert resolve_tier("medium", config)["model"] == "qwen3:8b"
    assert resolve_tier("frontier", config)["model"] == "qwen3:14b"


def test_default_tier_is_medium() -> None:
    config = cast(dict[str, Any], {"coding": {}})
    coder, model = resolve_coder_models(config["coding"], _config())
    assert (coder, model) == ("qwen3:8b", "qwen3:8b")


def test_legacy_overrides_win() -> None:
    coding: dict[str, Any] = {"model": "custom:9b", "coder": "custom-tag"}
    assert resolve_coder_models(coding, _config()) == ("custom-tag", "custom:9b")
    assert resolve_coder_models({"model": "solo"}, _config()) == ("solo", "solo")


def test_unknown_tier_is_typed_and_names_the_key() -> None:
    with pytest.raises(ConfigError) as exc:
        resolve_tier("giant", _config())
    assert "models.tiers" in str(exc.value)
    assert "coding.tier" in str(exc.value)


def test_snapshot_rows_carry_required_fields() -> None:
    for tier_row in MODEL_TIERS_DEFAULTS.values():
        assert set(tier_row) == {
            "provider",
            "model",
            "digest",
            "context_window",
            "snapshot_date",
        }
        assert tier_row["snapshot_date"] == "2026-11-25"


def test_default_coder_models_reads_runtime_config() -> None:
    coder, model = default_coder_models()
    # The shipped default is the medium tier's model (the current coder).
    assert model == resolve_tier("medium", _config())["model"]
    assert coder == model
