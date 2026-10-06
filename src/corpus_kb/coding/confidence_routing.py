from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Bands:
    """Configurable confidence bands for routing decisions."""

    high: float = 0.90
    mid: float = 0.70
    low: float = 0.50


@dataclass(frozen=True)
class RouteResult:
    """Result of confidence routing."""

    route: str
    confidence: float


def route_cell(
    decision: Literal["assign", "reject"],
    confidence: float,
    is_provisional: bool,
    quote_ok: bool,
    bands: Bands,
) -> RouteResult:
    """
    Route a coded cell based on decision, confidence, provenance, and band thresholds.

    Precedence order:
    1. Quote failure → manual_review
    2. Provisional assign → cap at mid band + provisional-hold
    3. Confidence >= high → high-conf-accept or high-conf-reject
    4. Confidence >= mid → mid-band
    5. low <= confidence < mid → escalate
    6. Confidence < low → low-conf-escalate (more urgent than a normal escalate)

    Args:
        decision: 'assign' or 'reject'
        confidence: model confidence [0, 1]
        is_provisional: whether the assignment is provisional (unquoted)
        quote_ok: whether a verbatim quote was successfully verified
        bands: Bands instance with high/mid/low thresholds

    Returns:
        RouteResult with route and confidence (may be capped)
    """
    # Precedence 1: Quote failure → manual_review
    if not quote_ok:
        return RouteResult(route="manual_review", confidence=confidence)

    # Precedence 2: Provisional assign → cap at mid + provisional-hold
    if is_provisional and decision == "assign":
        capped_confidence = min(confidence, bands.mid)
        return RouteResult(route="provisional-hold", confidence=capped_confidence)

    # Precedence 3: Confidence >= high → high-conf-{accept,reject}
    if confidence >= bands.high:
        route = "high-conf-accept" if decision == "assign" else "high-conf-reject"
        return RouteResult(route=route, confidence=confidence)

    # Precedence 4: Confidence >= mid → mid-band
    if confidence >= bands.mid:
        return RouteResult(route="mid-band", confidence=confidence)

    # Precedence 5: low <= confidence < mid → escalate
    if confidence >= bands.low:
        return RouteResult(route="escalate", confidence=confidence)

    # Precedence 6: Confidence < low → low-conf-escalate (more urgent: further
    # below the routing bands than a normal escalate, so it must not be
    # indistinguishable from the [low, mid) case downstream).
    return RouteResult(route="low-conf-escalate", confidence=confidence)


def spotcheck_reroute(
    flip_rate: float,
    n_cells: int,
    min_cells: int = 20,
    flip_cap: float = 0.10,
) -> tuple[bool, str]:
    """
    Determine whether a batch should be re-routed based on observed flip rate.

    A flip occurs when a model decision is contradicted by a human decision
    (e.g., the model assigned high-confidence but human rejected).

    Args:
        flip_rate: observed fraction of flips [0, 1]
        n_cells: number of cells evaluated
        min_cells: minimum cells before flip rate is credible (default 20)
        flip_cap: threshold flip rate above which to reroute (default 0.10)

    Returns:
        (should_reroute, reason) tuple
    """
    if n_cells < min_cells:
        return False, f"insufficient_sample (n={n_cells}, min={min_cells})"

    if flip_rate > flip_cap:
        return True, f"flip_rate_exceeded (observed={flip_rate:.2%}, cap={flip_cap:.2%})"

    return False, f"flip_rate_acceptable (observed={flip_rate:.2%}, cap={flip_cap:.2%})"
