"""P1-evaluable release gates (U43/U44/U48) — the real evidence readers.

Split from ``gates.py`` to honor the 250-line soft limit. These three gates
evaluate evidence P1 itself produces (the U43 canary report, the U44
determinism proof, the U48 consensus report); everything else in the
registry is a Wave-2 stub. Same contract: pure functions over
``(GateInputs, ResolvedProfile)``, no queries, honest statuses.
"""

from __future__ import annotations

from corpus_kb.research.release.gates import (
    GATE_CLUSTER_DETERMINISM,
    GATE_QUOTE_CONSENSUS,
    GATE_STRUCTURED_OUTPUT,
    GateInputs,
    GateResult,
)
from corpus_kb.research.release.profiles import ResolvedProfile


def gate_structured_output(inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
    """U43: the coding model's structured-output canary verdict.

    ``enforced`` -> pass; ``unknown`` -> not_evaluable; ``not_enforced``
    fails outright (honesty status carried in the reason).
    """
    canary = inputs.canary or {}
    status = str(canary.get("status", "unknown"))
    if status == "enforced":
        return GateResult(
            gate_id=GATE_STRUCTURED_OUTPUT,
            status="pass",
            reason="structured-output constraint enforced on the coding model",
            value={
                "status": status,
                "first_attempt_valid_rate": canary.get("first_attempt_valid_rate"),
            },
        )
    if status == "unknown":
        return GateResult(
            gate_id=GATE_STRUCTURED_OUTPUT,
            status="not_evaluable",
            reason="structured_output: unknown — canary did not complete",
            value=canary,
        )
    return GateResult(
        gate_id=GATE_STRUCTURED_OUTPUT,
        status="fail",
        reason="structured_output: not_enforced — the backend ignores the format "
        "constraint (HTTP 200 with unconstrained text)",
        value=canary,
    )


def gate_clustering_determinism(inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
    """U44: the determinism proof must be complete and the sweep converged."""
    det = inputs.determinism or {}
    required = ("embedding_matrix_sha256", "seed", "params", "library_versions")
    missing = [k for k in required if not det.get(k)]
    if missing:
        return GateResult(
            gate_id=GATE_CLUSTER_DETERMINISM,
            status="not_evaluable",
            reason=f"determinism proof incomplete (missing: {missing})",
            value=det,
        )
    sweep = det.get("sweep") or {}
    converged = sweep.get("converged")
    if converged is None:
        return GateResult(
            gate_id=GATE_CLUSTER_DETERMINISM,
            status="not_evaluable",
            reason="no converged seed sweep recorded; the deterministic run alone is not evidence",
            value=det,
        )
    if not converged:
        return GateResult(
            gate_id=GATE_CLUSTER_DETERMINISM,
            status="fail",
            reason="seed sweep not_converged: cluster-count variance exceeds the admin threshold",
            value=sweep,
        )
    return GateResult(
        gate_id=GATE_CLUSTER_DETERMINISM,
        status="pass",
        reason="fixed random_state/seed with a converged >=10-seed sweep",
        value=det,
    )


def gate_quote_consensus(inputs: GateInputs, _profile: ResolvedProfile) -> GateResult:
    """U48: multi-run consensus + verbatim-quote evidence over candidates."""
    consensus = inputs.consensus or {}
    if not consensus:
        return GateResult(
            gate_id=GATE_QUOTE_CONSENSUS,
            status="not_evaluable",
            reason="no consensus report recorded",
        )
    runs = int(consensus.get("n_runs", 0) or 0)
    kept = int(consensus.get("themes_kept", 0) or 0)
    rejected = int(consensus.get("quote_not_found_rejected", 0) or 0)
    if runs < 2:
        return GateResult(
            gate_id=GATE_QUOTE_CONSENSUS,
            status="not_evaluable",
            reason="insufficient_data: fewer than 2 candidate runs",
            value=consensus,
        )
    if kept <= 0:
        return GateResult(
            gate_id=GATE_QUOTE_CONSENSUS,
            status="fail",
            reason="no theme reached the configured run fraction",
            value=consensus,
        )
    return GateResult(
        gate_id=GATE_QUOTE_CONSENSUS,
        status="pass",
        reason=f"{kept} consensus theme(s) over {runs} runs; {rejected} candidate(s) "
        "rejected with quote_not_found",
        value={
            "themes_kept": kept,
            "n_runs": runs,
            "min_run_fraction": consensus.get("min_run_fraction"),
            "quote_not_found_rejected": rejected,
        },
    )
