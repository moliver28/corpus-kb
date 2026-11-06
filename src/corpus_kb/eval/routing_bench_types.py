"""Types shared by the routing bench and its calibration sweep.

A leaf by design: no intra-eval imports. Both consumers point here —
the sweep (``eval.routing_calibrate``) and the scorer
(``eval.scoring``) — so the type-only edge between those two modules
is gone: the static analyzer read it as a real import cycle, and the
runtime graph never needed it.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RoutingGoldenRow"]


@dataclass(frozen=True)
class RoutingGoldenRow:
    """One routing golden row: the question and the acceptable methods."""

    question: str
    expected_method_ids: tuple[str, ...]
