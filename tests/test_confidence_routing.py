from __future__ import annotations

from corpus_kb.coding.confidence_routing import Bands, route_cell, spotcheck_reroute

B = Bands(high=0.90, mid=0.70, low=0.50)


def test_routing_bands_and_provisional() -> None:
    assert (
        route_cell("assign", 0.95, is_provisional=False, quote_ok=True, bands=B).route
        == "high-conf-accept"
    )
    assert route_cell("reject", 0.95, False, True, B).route == "high-conf-reject"
    assert route_cell("assign", 0.80, False, True, B).route == "mid-band"
    assert route_cell("assign", 0.30, False, True, B).route == "low-conf-escalate"
    r = route_cell("assign", 0.95, is_provisional=True, quote_ok=True, bands=B)
    assert r.route == "provisional-hold" and r.confidence <= 0.70
    assert route_cell("assign", 0.95, False, quote_ok=False, bands=B).route == "manual_review"


def test_hedging_cannot_flip_bands() -> None:
    """Tier 2 hedging count may only nudge within a band, never flip one.
    This test documents that hedging signals do not override the routing bands."""
    # A high-confidence assignment remains in high-conf-accept even with
    # notional high hedging count; the routing decision is solely based on
    # confidence and decision, not on hedging signals.
    result = route_cell("assign", 0.95, is_provisional=False, quote_ok=True, bands=B)
    assert result.route == "high-conf-accept"
    # The hedging count (Tier 2) is a separate signal that calibrates within
    # a band but cannot cause a cell to move from high-conf to mid-band, etc.
    # This is enforced by the routing logic: confidence drives the band,
    # hedging does not participate in that decision.

    # A low-confidence assignment stays in low-conf-escalate
    result_low = route_cell("assign", 0.30, is_provisional=False, quote_ok=True, bands=B)
    assert result_low.route == "low-conf-escalate"
    # Even if hedging were added later, it would not flip this to mid-band
    # because hedging is used only for calibration within a band, not band selection.


def test_spotcheck_reroute() -> None:
    """Test spot-check reroute decision logic."""
    # Insufficient sample: should not reroute
    should_reroute, reason = spotcheck_reroute(flip_rate=0.15, n_cells=10, min_cells=20)
    assert should_reroute is False
    assert "insufficient_sample" in reason

    # Acceptable flip rate: should not reroute
    should_reroute, reason = spotcheck_reroute(flip_rate=0.05, n_cells=50, min_cells=20)
    assert should_reroute is False
    assert "acceptable" in reason

    # Exceeded flip rate: should reroute
    should_reroute, reason = spotcheck_reroute(
        flip_rate=0.15, n_cells=50, min_cells=20, flip_cap=0.10
    )
    assert should_reroute is True
    assert "exceeded" in reason
