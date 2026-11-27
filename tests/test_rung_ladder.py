"""Rung ladder (TD-06): order, configuration skips, band halt, move-up rule."""

from __future__ import annotations

from typing import Any

from corpus_kb.coding.model_tiers import MODEL_TIERS_DEFAULTS
from corpus_kb.coding.rung_ladder import (
    LADDER_ORDER,
    RUNG_HUMAN,
    band_halt,
    configured_rungs,
    first_rung,
    first_rung_moveup,
    next_rung,
)


def _config(**coding: Any) -> dict[str, Any]:
    return {
        "models": {"tiers": MODEL_TIERS_DEFAULTS},
        "coding": {"tier": "medium", "cloud": {"consent": False, "models": {}}, **coding},
    }


def test_ladder_order_is_cheapest_first() -> None:
    assert LADDER_ORDER == (
        "local_small",
        "local_medium",
        "cloud_small",
        "cloud_medium",
        "cloud_large",
        "human",
    )


def test_local_only_site_skips_cloud_rungs() -> None:
    assert configured_rungs(_config()) == ["local_small", "local_medium", "human"]


def test_consent_false_disables_cloud_rungs_even_with_models() -> None:
    rungs = configured_rungs(
        _config(cloud={"consent": False, "models": {"small": "claude-x", "medium": "claude-y"}})
    )
    assert "cloud_small" not in rungs


def test_consent_true_enables_named_cloud_rungs() -> None:
    rungs = configured_rungs(_config(cloud={"consent": True, "models": {"small": "claude-x"}}))
    assert rungs == ["local_small", "local_medium", "cloud_small", "human"]


def test_first_rung_follows_coding_tier() -> None:
    rungs = ["local_small", "local_medium", "human"]
    assert first_rung(rungs, "small") == "local_small"
    assert first_rung(rungs, "medium") == "local_medium"
    assert first_rung(rungs, "frontier") == "local_medium"


def test_next_rung_and_human_queue() -> None:
    rungs = ["local_medium", "cloud_small", "human"]
    assert next_rung("local_medium", rungs) == "cloud_small"
    assert next_rung("cloud_small", rungs) == RUNG_HUMAN  # no model rung left
    assert next_rung(RUNG_HUMAN, rungs) is None  # the human rung is terminal
    assert next_rung("cloud_small", ["local_medium", "cloud_small"]) is None


def test_band_halt_inside_and_outside() -> None:
    _, ok = band_halt(2, 20)  # 10 percent: inside the band
    assert ok == ""
    _, ok = band_halt(4, 20)  # 20 percent: inside (inclusive)
    assert ok == ""
    halt, reason = band_halt(8, 20)  # 40 percent: outside
    assert halt is True
    assert reason.startswith("cycle_halted")
    halt, reason = band_halt(0, 20)  # 0 percent: outside the band too
    assert halt is True


def test_band_halt_needs_enough_units() -> None:
    halt, reason = band_halt(5, 8)  # 62 percent but only 8 units: noise
    assert halt is False
    assert "not checked" in reason


def test_first_rung_moveup_rule() -> None:
    assert first_rung_moveup(6, 10) is True  # > half left the first rung
    assert first_rung_moveup(5, 10) is False
    assert first_rung_moveup(0, 0) is False


def test_halt_message_is_plain_english() -> None:
    _, reason = band_halt(9, 20)
    sentences = [s for s in reason.split("; ") if s]
    assert all(len(s.split()) <= 20 for s in sentences)
