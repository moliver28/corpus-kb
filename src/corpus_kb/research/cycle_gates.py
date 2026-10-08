"""Cycle halt gates (todo 20) — evaluated over EXISTING stage outputs only.

PURE ORCHESTRATION (r7 Momus M-1): the cycle introduces NO new analytics.
Every gate reads artifacts the named surfaces already produced (run
summaries, run checkpoints, code_registry theories, the schema-pinned
governance report); the only governance added here is the HALT decision
itself.

HARD FLOOR (v5 §9.6/§14 codebook governance): ``codebook_promotion`` is
always a halt gate and promotion is never automated. ``research.cycle.halt_on``
may only ADD gates — removing the floor from config is a no-op by
construction because :func:`effective_halt_on` unions the floor back in.

Documented gate mappings (r11, no new math):
  * gray_zone_escalation ALSO fires on conformal-coverage violations
    (empirical < nominal coverage from the deductive run checkpoint);
  * drift_alarm ALSO fires on DBCV relative-validity drops >20%
    (the inductive engine's own ``dbcv_drop_flag``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GATE_CODEBOOK_PROMOTION = "codebook_promotion"
GATE_INTERPRETIVE_CODE_REVIEW = "interpretive_code_review"
GATE_GRAY_ZONE_ESCALATION = "gray_zone_escalation"
GATE_DRIFT_ALARM = "drift_alarm"
GATE_OVERLAP_CONFLICT = "overlap_conflict"
GATE_THRESHOLD_UNRELIABLE = "threshold_unreliable"
GATE_HUMAN_PARITY_BREACH = "human_parity_breach"

HARD_FLOOR_GATES = frozenset({GATE_CODEBOOK_PROMOTION})

# The config default (research.cycle.halt_on). codebook_promotion lives in
# HARD_FLOOR_GATES, not here: the floor is not a config decision.
DEFAULT_HALT_ON: tuple[str, ...] = (
    GATE_INTERPRETIVE_CODE_REVIEW,
    GATE_GRAY_ZONE_ESCALATION,
    GATE_DRIFT_ALARM,
    GATE_OVERLAP_CONFLICT,
    GATE_THRESHOLD_UNRELIABLE,
    GATE_HUMAN_PARITY_BREACH,
)

ALL_GATES = frozenset(DEFAULT_HALT_ON) | HARD_FLOOR_GATES

GATE_EXIT_CODES: dict[str, int] = {
    GATE_CODEBOOK_PROMOTION: 10,
    GATE_INTERPRETIVE_CODE_REVIEW: 11,
    GATE_GRAY_ZONE_ESCALATION: 12,
    GATE_DRIFT_ALARM: 13,
    GATE_OVERLAP_CONFLICT: 14,
    GATE_THRESHOLD_UNRELIABLE: 15,
    GATE_HUMAN_PARITY_BREACH: 16,
}
EXIT_APPROVAL_DENIED = 20
EXIT_AWAITING_APPROVAL = 21
EXIT_UNAVAILABLE = 2


class UnknownGateError(ValueError):
    """A research.cycle.halt_on entry is not a known gate name."""


def effective_halt_on(configured: list[str] | tuple[str, ...] | None) -> frozenset[str]:
    """Config halt_on UNION the hard floor; unknown names rejected."""
    names = list(configured or ())
    unknown = sorted(n for n in names if n not in ALL_GATES)
    if unknown:
        raise UnknownGateError(
            f"unknown research.cycle.halt_on gates: {unknown} (known: {sorted(ALL_GATES)})"
        )
    return frozenset(names) | HARD_FLOOR_GATES


@dataclass(frozen=True)
class GateFinding:
    """One raised gate: what fired, why, and the evidence behind it."""

    gate: str
    reason: str
    detail: dict[str, object] = field(default_factory=dict)


def _theories(codes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in codes if isinstance(c.get("theory"), dict)]


def gate_from_inductive(summary: dict[str, Any]) -> list[GateFinding]:
    """Checkpoint after the inductive pass: drift_alarm only.

    The inductive engine already decided the flag (inductive_run's
    dbcv_drop_flag vs the previous run's DBCV baseline); the cycle merely
    surfaces it.
    """
    findings: list[GateFinding] = []
    if bool(summary.get("dbcv_drop_flag")):
        findings.append(
            GateFinding(
                GATE_DRIFT_ALARM,
                "cluster validity dropped >20% vs the previous run's DBCV baseline",
                {
                    "dbcv_relative_validity": summary.get("dbcv_relative_validity"),
                    "dbcv_baseline": summary.get("dbcv_baseline"),
                },
            )
        )
    return findings


def gate_pending_proposals(pending: int, run_id: str) -> GateFinding | None:
    """The HARD-FLOOR gate: proposals from this cycle await a human decision."""
    if pending <= 0:
        return None
    return GateFinding(
        GATE_CODEBOOK_PROMOTION,
        f"{pending} proposed code(s) from this cycle await human promotion",
        {"pending_proposals": pending, "inductive_run_id": run_id},
    )


def gate_from_theories(codes: list[dict[str, Any]]) -> list[GateFinding]:
    """Checkpoint after the deductive run: threshold + interpretive gates.

    Reads each code's OWN theory block (calibration wrote
    thresholds.threshold_unreliable; seeding wrote is_interpretive) — the
    same blocks the deductive engine scored with, reread verbatim.
    """
    findings: list[GateFinding] = []
    unreliable = [
        str(c["code_id"])
        for c in _theories(codes)
        if bool((c["theory"].get("thresholds") or {}).get("threshold_unreliable"))
    ]
    if unreliable:
        findings.append(
            GateFinding(
                GATE_THRESHOLD_UNRELIABLE,
                "gold below the 20-per-code bar: thresholds are provisional",
                {"codes": unreliable},
            )
        )
    interpretive = [
        str(c["code_id"]) for c in _theories(codes) if bool(c["theory"].get("is_interpretive"))
    ]
    if interpretive:
        findings.append(
            GateFinding(
                GATE_INTERPRETIVE_CODE_REVIEW,
                "interpretive codes always route through human review",
                {"codes": interpretive},
            )
        )
    return findings


def gate_from_deductive(
    summary: dict[str, Any], run_checkpoint: dict[str, Any] | None
) -> list[GateFinding]:
    """Checkpoint after the deductive run: gray-zone escalation.

    Fires when any assignment routed to review (the gray-zone minority) OR
    the run's conformal block shows empirical coverage below nominal (the
    documented conformal-coverage mapping, r11).
    """
    review = int(summary.get("review") or 0)
    violation = False
    conformal = (run_checkpoint or {}).get("conformal")
    if isinstance(conformal, dict):
        nominal = conformal.get("nominal_coverage")
        empirical = conformal.get("empirical_coverage")
        if isinstance(nominal, (int, float)) and isinstance(empirical, (int, float)):
            violation = float(empirical) < float(nominal)
    if review <= 0 and not violation:
        return []
    reasons = []
    if review > 0:
        reasons.append(f"{review} assignment(s) routed to review")
    if violation:
        reasons.append("conformal empirical coverage below nominal")
    return [
        GateFinding(
            GATE_GRAY_ZONE_ESCALATION,
            "; ".join(reasons),
            {
                "review": review,
                "conformal_nominal": (conformal or {}).get("nominal_coverage")
                if isinstance(conformal, dict)
                else None,
                "conformal_empirical": (conformal or {}).get("empirical_coverage")
                if isinstance(conformal, dict)
                else None,
            },
        )
    ]


def gate_from_report(report: dict[str, Any]) -> list[GateFinding]:
    """Checkpoint after the report: overlap conflicts + human-parity breach.

    The report is the ONE artifact carrying the overlap matrix and the G3
    audit, so both gates evaluate there. human_parity_breach consumes the
    G3 halt_gate verbatim (g3_audit.HUMAN_PARITY_BREACH_GATE, todo 16's
    human-only flip).
    """
    findings: list[GateFinding] = []
    overlap = report.get("overlap")
    keywords = report.get("keywords")
    flagged = overlap.get("flagged_pairs") if isinstance(overlap, dict) else None
    conflicts = keywords.get("conflicts") if isinstance(keywords, dict) else None
    if flagged or conflicts:
        findings.append(
            GateFinding(
                GATE_OVERLAP_CONFLICT,
                "code overlap and/or keyword conflicts need a merge decision",
                {
                    "flagged_pairs": len(flagged) if isinstance(flagged, list) else 0,
                    "keyword_conflicts": len(conflicts) if isinstance(conflicts, list) else 0,
                },
            )
        )
    g3 = report.get("g3_audit")
    if isinstance(g3, dict) and (
        g3.get("halt_gate") == GATE_HUMAN_PARITY_BREACH or bool(g3.get("parity_breach"))
    ):
        findings.append(
            GateFinding(
                GATE_HUMAN_PARITY_BREACH,
                "LLM-vs-human alpha below 0.60: affected codes route human-only",
                {
                    "codes": [
                        v.get("code_id")
                        for v in (g3.get("human_parity") or [])
                        if isinstance(v, dict) and v.get("breach")
                    ]
                },
            )
        )
    return findings
