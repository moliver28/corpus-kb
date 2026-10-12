"""Release gate framework (U4/U10/U43/U44/U48; v6 §6) — pure evaluation.

A gate is a PURE function ``(GateInputs, ResolvedProfile) -> GateResult``:
no queries inside gate functions, ever. Read helpers (``gate_reads.py``,
``cycle_reads.py`` style) assemble :class:`GateInputs` before evaluation;
``assess`` composes the full registry.

HONESTY STATUSES: a gate result is one of ``pass|fail|not_evaluable|waived``.
The finer honesty vocabulary (``insufficient_data``, ``not_enforced``,
``invalid_output``) travels inside ``reason``/``value`` — a gate never
upgrades missing evidence into a pass. Every registered gate is a real
evaluation over its evidence block: the U7-U19 gates live in the
``gate_*.py`` modules (wave 2), the P1 evidence readers in
``evidence_gates.py``.
"""

from __future__ import annotations

from corpus_kb.research.release.evidence_gates import (
    gate_clustering_determinism,
    gate_quote_consensus,
    gate_structured_output,
)
from corpus_kb.research.release.gate_annotators import (
    gate_alt_test,
    gate_prediction_powered,
    gate_prompt_stability,
)
from corpus_kb.research.release.gate_codebook import gate_definition_completeness
from corpus_kb.research.release.gate_machine_profile import gate_machine_profile
from corpus_kb.research.release.gate_measures import (
    gate_exhaustiveness,
    gate_saturation,
)
from corpus_kb.research.release.gate_partitions import (
    gate_boundary_agreement,
    gate_partition_independence,
)
from corpus_kb.research.release.gate_performance import (
    gate_heldout_per_code_f1,
    gate_stratum_disparity,
)
from corpus_kb.research.release.gate_reliability import gate_reliability_alpha
from corpus_kb.research.release.gate_routing import (
    gate_agreement_calibration,
    gate_agreement_routing,
)
from corpus_kb.research.release.gate_stability import (
    gate_audit_sample_design,
    gate_content_stability,
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
    "WAVE2_GATES",
    "GateFn",
    "GateInputs",
    "GateResult",
    "GateStatus",
    "UnknownGateError",
    "assess",
]


GATES: dict[str, GateFn] = {
    GATE_DEFINITION_COMPLETENESS: gate_definition_completeness,
    GATE_BOUNDARY_AGREEMENT: gate_boundary_agreement,
    GATE_PARTITION_INDEPENDENCE: gate_partition_independence,
    GATE_MACHINE_PROFILE: gate_machine_profile,
    GATE_HELDOUT_F1: gate_heldout_per_code_f1,
    GATE_PROMPT_STABILITY: gate_prompt_stability,
    GATE_ALT_TEST: gate_alt_test,
    GATE_PREDICTION_POWERED: gate_prediction_powered,
    GATE_STRATUM_DISPARITY: gate_stratum_disparity,
    GATE_CONTENT_STABILITY: gate_content_stability,
    GATE_AUDIT_SAMPLE: gate_audit_sample_design,
    GATE_AGREEMENT_CALIBRATION: gate_agreement_calibration,
    GATE_AGREEMENT_ROUTING: gate_agreement_routing,
    GATE_RELIABILITY_ALPHA: gate_reliability_alpha,
    GATE_EXHAUSTIVENESS: gate_exhaustiveness,
    GATE_SATURATION: gate_saturation,
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
