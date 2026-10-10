"""Shared gate-framework types and ids (imported by gates + evidence_gates).

Split out of gates.py to break the registry/evidence import cycle: both the
framework (``gates.py``) and the P1 evidence readers (``evidence_gates.py``)
depend on these, and neither depends on the other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from corpus_kb.research.release.profiles import ResolvedProfile

GateStatus = Literal["pass", "fail", "not_evaluable", "waived"]

# U7-U19 gate ids (v6 §7). Stubs until Wave 2 delivers their inputs.
GATE_DEFINITION_COMPLETENESS = "definition_completeness"
GATE_BOUNDARY_AGREEMENT = "boundary_agreement"
GATE_PARTITION_INDEPENDENCE = "partition_independence"
GATE_MACHINE_PROFILE = "machine_profile"
GATE_HELDOUT_F1 = "heldout_per_code_f1"
GATE_PROMPT_STABILITY = "prompt_stability"
GATE_ALT_TEST = "alternative_annotator_test"
GATE_PREDICTION_POWERED = "prediction_powered_estimates"
GATE_STRATUM_DISPARITY = "stratum_disparity"
GATE_CONTENT_STABILITY = "content_stability"
GATE_AUDIT_SAMPLE = "audit_sample_design"
GATE_AGREEMENT_CALIBRATION = "agreement_calibration"
GATE_AGREEMENT_ROUTING = "agreement_routing"

# Gates P1 can evaluate from P1-built evidence.
GATE_RELIABILITY_ALPHA = "reliability_alpha"
GATE_EXHAUSTIVENESS = "exhaustiveness"
GATE_SATURATION = "saturation"
GATE_STRUCTURED_OUTPUT = "structured_output_enforcement"
GATE_CLUSTER_DETERMINISM = "clustering_determinism"
GATE_QUOTE_CONSENSUS = "quote_consensus"

WAVE2_GATES: tuple[str, ...] = (
    GATE_DEFINITION_COMPLETENESS,
    GATE_BOUNDARY_AGREEMENT,
    GATE_PARTITION_INDEPENDENCE,
    GATE_MACHINE_PROFILE,
    GATE_HELDOUT_F1,
    GATE_PROMPT_STABILITY,
    GATE_ALT_TEST,
    GATE_PREDICTION_POWERED,
    GATE_STRATUM_DISPARITY,
    GATE_CONTENT_STABILITY,
    GATE_AUDIT_SAMPLE,
    GATE_AGREEMENT_CALIBRATION,
    GATE_AGREEMENT_ROUTING,
    GATE_RELIABILITY_ALPHA,
    GATE_EXHAUSTIVENESS,
    GATE_SATURATION,
)

STUB_REASON = "inputs pending wave 2"


@dataclass(frozen=True)
class GateInputs:
    """Pre-fetched evidence; gates stay pure over this snapshot."""

    manifest: dict[str, object] | None = None
    canary: dict[str, object] | None = None
    determinism: dict[str, object] | None = None
    consensus: dict[str, object] | None = None
    metrics: dict[str, object] | None = None
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GateResult:
    """One gate's honest outcome (never a pass from missing data)."""

    gate_id: str
    status: GateStatus
    reason: str
    value: object = None
    threshold: object = None
    evidence_refs: tuple[str, ...] = ()


GateFn = Callable[[GateInputs, ResolvedProfile], GateResult]
