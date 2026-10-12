"""Pure gate evidence builders (U19) — read-model inputs to the gates.

Like the cycle's ``cycle_reads.py``: the EVIDENCE math is assembled here
from per-run label blocks the stages already produced; the gate decision
itself stays pure (``gates.py`` / ``cycle_gates.py``). Caller-less until
the cycle's inductive checkpoint wires it (adversarial review F2).
"""

from __future__ import annotations

import math
from typing import Any

from corpus_kb.coding.uncertainty.cross_run_agreement import cross_run_agreement_batch

__all__ = ["agreement_routing_evidence"]


def agreement_routing_evidence(
    run_label_blocks: list[dict[str, Any]], routed_ids: list[str]
) -> dict[str, object]:
    """Pure: U19 ledger from per-run label blocks ({unit_id: label} each).

    Computes the per-unit cross-run agreement (wave-1
    ``cross_run_agreement_batch``); a unit is ``routed`` when its id appears
    in ``routed_ids`` (the gray-zone queue's current membership). Fewer than
    two label blocks means no agreement is measured — every unit scores
    ``None`` (honest not-measured, never 0.0 or 1.0).
    """
    if not run_label_blocks:
        return {"units": []}
    unit_ids = sorted({str(k) for block in run_label_blocks for k in block})
    labels_per_unit = [[block.get(uid) for block in run_label_blocks] for uid in unit_ids]
    scores = cross_run_agreement_batch(labels_per_unit)
    routed = set(routed_ids)
    units: list[dict[str, object]] = []
    for uid, score in zip(unit_ids, scores, strict=True):
        value = float(score)
        units.append(
            {
                "unit_id": uid,
                "agreement": None if math.isnan(value) else value,
                "routed": uid in routed,
            }
        )
    return {"units": units}
