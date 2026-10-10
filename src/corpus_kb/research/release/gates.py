"""Release gate framework (U4/U10/U43/U44/U48; v6 §6) — pure evaluation.

A gate is a PURE function ``(GateInputs, ResolvedProfile) -> GateResult``:
no queries inside gate functions, ever. Read helpers (``cycle_reads.py``
style) assemble :class:`GateInputs` before evaluation; ``assess`` composes
the full registry.

HONESTY STATUSES: a gate result is one of ``pass|fail|not_evaluable|waived``.
The finer honesty vocabulary (``insufficient_data``, ``not_enforced``,
``invalid_output``) travels inside ``reason``/``value`` — a gate never
upgrades missing evidence into a pass. Stubs for the Wave-2 gate ids
(U7-U19) return ``not_evaluable`` with reason ``inputs pending wave 2`` so
``assess`` composes the complete set today and Wave 2 fills them in.
"""

from __future__ import annotations

from corpus_kb.research.release.evidence_gates import (
    gate_clustering_determinism,
    gate_quote_consensus,
    gate_structured_output,
)
from corpus_kb.research.release.gate_types import (
    GATE_AGREEMENT_CALIBRATION,
    GATE_AGREEMENT_ROUTING,
    GATE_ALT_TEST,
    GATE_AUDIT_SAMPLE,
    GATE_BOUNDARY_AGREEMENT,
    GATE_CLUSTER_DETERMINISM,
    GATE_CONTENT_STABILITY,
    GATE_DEFINITION_COMPLETENESS,
    GATE_EXHAUSTIVENESS,
    GATE_HELDOUT_F1,
    GATE_MACHINE_PROFILE,
    GATE_PARTITION_INDEPENDENCE,
    GATE_PREDICTION_POWERED,
    GATE_PROMPT_STABILITY,
    GATE_QUOTE_CONSENSUS,
    GATE_RELIABILITY_ALPHA,
    GATE_SATURATION,
    GATE_STRATUM_DISPARITY,
    GATE_STRUCTURED_OUTPUT,
    STUB_REASON,
    WAVE2_GATES,
    GateFn,
    GateInputs,
    GateResult,
    GateStatus,
)
from corpus_kb.research.release.profiles import ResolvedProfile

__all__ = [
    "ALL_RELEASE_GATES",
    "GATES",
    "STUB_REASON",
    "WAVE2_GATES",
    "GateFn",
    "GateInputs",
    "GateResult",
    "GateStatus",
    "UnknownGateError",
    "assess",
]


def _stub(gate_id: str) -> GateFn:
    def evaluate(_inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
        return GateResult(
            gate_id=gate_id,
            status="not_evaluable",
            reason=STUB_REASON,
        )

    return evaluate


GATES: dict[str, GateFn] = {
    GATE_DEFINITION_COMPLETENESS: _stub(GATE_DEFINITION_COMPLETENESS),
    GATE_BOUNDARY_AGREEMENT: _stub(GATE_BOUNDARY_AGREEMENT),
    GATE_PARTITION_INDEPENDENCE: _stub(GATE_PARTITION_INDEPENDENCE),
    GATE_MACHINE_PROFILE: _stub(GATE_MACHINE_PROFILE),
    GATE_HELDOUT_F1: _stub(GATE_HELDOUT_F1),
    GATE_PROMPT_STABILITY: _stub(GATE_PROMPT_STABILITY),
    GATE_ALT_TEST: _stub(GATE_ALT_TEST),
    GATE_PREDICTION_POWERED: _stub(GATE_PREDICTION_POWERED),
    GATE_STRATUM_DISPARITY: _stub(GATE_STRATUM_DISPARITY),
    GATE_CONTENT_STABILITY: _stub(GATE_CONTENT_STABILITY),
    GATE_AUDIT_SAMPLE: _stub(GATE_AUDIT_SAMPLE),
    GATE_AGREEMENT_CALIBRATION: _stub(GATE_AGREEMENT_CALIBRATION),
    GATE_AGREEMENT_ROUTING: _stub(GATE_AGREEMENT_ROUTING),
    GATE_RELIABILITY_ALPHA: _stub(GATE_RELIABILITY_ALPHA),
    GATE_EXHAUSTIVENESS: _stub(GATE_EXHAUSTIVENESS),
    GATE_SATURATION: _stub(GATE_SATURATION),
    GATE_STRUCTURED_OUTPUT: gate_structured_output,
    GATE_CLUSTER_DETERMINISM: gate_clustering_determinism,
    GATE_QUOTE_CONSENSUS: gate_quote_consensus,
}

ALL_RELEASE_GATES: frozenset[str] = frozenset(GATES)


class UnknownGateError(ValueError):
    """A required-gates entry is not a registered release gate."""


def assess(inputs: GateInputs, profile: ResolvedProfile) -> dict[str, object]:
    """Compose EVERY registered gate (write nothing — release evidence only).

    The result carries per-gate results plus release-blocking counters: how
    many of the profile's REQUIRED gates are fail / not_evaluable, and the
    pass-or-waived verdict ``assess`` would suggest (waivers are the service's
    to apply, so this raw verdict ignores them).
    """
    results = [fn(inputs, profile) for fn in GATES.values()]
    by_id = {r.gate_id: r for r in results}
    required = [gid for gid in profile.required_gates if gid in by_id]
    unknown = [gid for gid in profile.required_gates if gid not in by_id]
    if unknown:
        raise UnknownGateError(f"profile requires unknown gate ids: {unknown}")
    blocking = [
        r for r in (by_id[gid] for gid in required) if r.status in ("fail", "not_evaluable")
    ]
    raw_ok = all(by_id[gid].status == "pass" for gid in required)
    return {
        "profile": profile.name,
        "enforcement": profile.enforcement,
        "gate_results": [
            {
                "gate_id": r.gate_id,
                "status": r.status,
                "reason": r.reason,
                "value": r.value,
                "threshold": r.threshold,
                "evidence_refs": list(r.evidence_refs),
            }
            for r in results
        ],
        "required_gate_ids": list(required),
        "required_failing": [r.gate_id for r in blocking if r.status == "fail"],
        "required_not_evaluable": [r.gate_id for r in blocking if r.status == "not_evaluable"],
        "all_required_pass_without_waivers": raw_ok,
    }
