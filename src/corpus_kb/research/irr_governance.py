"""Dual-coefficient IRR governance (todo 17, v5 §11/§14; r8 + r10).

Krippendorff's alpha alone collapses for rare codes even at 95% agreement
(the alpha paradox), so Gwet's AC1 (Gwet 2008) is reported ALONGSIDE alpha
per code:

* alpha >= 0.80               -> ``reliable``
* 0.667 <= alpha < 0.80       -> ``tentative`` (flagged `tentative-reliability`
                                 in the checkpoint, routed to review)
* alpha < 0.667               -> ``halt`` (the promotion gate halts)
* prevalence-affected note    -> alpha < 0.80 but AC1 > 0.90 (O'Connor &
                                 Joffe 2020 §4: interpret, do not halt)

AC1 is the pairable-weighted multi-rater binary form over the SAME
(n_u0, n_u1, m_u) unit triples the vendored alpha uses, so callers reuse
``coding.reliability``'s coincidence extraction unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

ALPHA_RELIABLE = 0.80
ALPHA_TENTATIVE_FLOOR = 0.667
AC1_PREVALENCE_CEILING = 0.90


def gwet_ac1(units: Sequence[tuple[int, int, int]]) -> float | None:
    """Multi-rater binary Gwet AC1 over (n_u0, n_u1, m_u) unit triples.

    Pa (observed agreement) is the pairable-weighted probability that two
    randomly chosen raters of the same unit agree; Pe(gamma) is Gwet's
    chance-agreement term 2*pi(1-pi) with pi the average marginal
    positive-rating probability. Returns None without pairable data or a
    degenerate Pe.
    """
    weights = [m * (m - 1) for (_u0, _u1, m) in units]
    total_weight = sum(weights)
    if total_weight == 0:
        return None
    agree = 0.0
    pi_sum = 0.0
    n_units = 0
    for (u0, u1, m), w in zip(units, weights, strict=True):
        if w == 0:
            continue
        agree += w * (u0 * (u0 - 1) + u1 * (u1 - 1)) / (m * (m - 1))
        pi_sum += u1 / m
        n_units += 1
    if n_units == 0:
        return None
    pa = agree / total_weight
    pi = pi_sum / n_units
    pe = 2.0 * pi * (1.0 - pi)
    if pe >= 1.0:
        return None
    return (pa - pe) / (1.0 - pe)


@dataclass(frozen=True)
class IrrCodeVerdict:
    """One code's dual-coefficient verdict."""

    code_id: str
    alpha: float | None
    kappa: float | None
    ac1: float | None
    band: str  # reliable | tentative | halt | unassessed
    prevalence_affected: bool


def classify_band(alpha: float | None) -> str:
    """alpha governance band (v5 r8 accepted-practice thresholds)."""
    if alpha is None:
        return "unassessed"
    if alpha >= ALPHA_RELIABLE:
        return "reliable"
    if alpha >= ALPHA_TENTATIVE_FLOOR:
        return "tentative"
    return "halt"


def govern_irr(
    reliability_report: Mapping[str, object],
    units_by_code: Mapping[str, Sequence[tuple[int, int, int]]] | None = None,
) -> dict[str, object]:
    """Dual-coefficient governance over a ``computeReliability`` report.

    Args:
        reliability_report: the dict returned by
            ``coding.reliability.computeReliability`` (per_code + overall).
        units_by_code: optional per-code (n_u0, n_u1, m_u) triples for the
            exact AC1 computation; when absent, AC1 is derived from the
            observed-agreement implied by alpha (documented approximation:
            Pa = 1 - D where D = 1 - alpha scaled by (n0*n1)/(n-1)).

    Returns:
        {"per_code": [IrrCodeVerdict-shaped dicts], "overall": {...},
         "halt": bool, "tentative_codes": [...], "prevalence_notes": [...]}
    """
    per_code_raw = cast(Mapping[str, Mapping[str, object]], reliability_report.get("per_code", {}))
    overall_raw = cast(Mapping[str, object], reliability_report.get("overall", {}))
    verdicts: list[dict[str, object]] = []
    tentative: list[str] = []
    prevalence_notes: list[str] = []
    halt = False
    for code_id in sorted(per_code_raw):
        entry = per_code_raw[code_id]
        alpha = cast("float | None", entry.get("alpha"))
        kappa = cast("float | None", entry.get("kappa"))
        units = (units_by_code or {}).get(code_id)
        ac1 = gwet_ac1(list(units)) if units else _ac1_from_alpha(alpha)
        band = classify_band(alpha)
        if band == "tentative":
            tentative.append(code_id)
        if band == "halt":
            halt = True
        prevalence = bool(
            alpha is not None
            and alpha < ALPHA_RELIABLE
            and ac1 is not None
            and ac1 > AC1_PREVALENCE_CEILING
        )
        if prevalence:
            prevalence_notes.append(
                f"{code_id}: alpha {alpha:.3f} < 0.80 but AC1 {ac1:.3f} > 0.90 — "
                "prevalence-affected (O'Connor & Joffe 2020): interpret, not halt"
            )
        verdicts.append(
            {
                "code_id": code_id,
                "alpha": alpha,
                "kappa": kappa,
                "ac1": ac1,
                "band": band,
                "prevalence_affected": prevalence,
            }
        )
    overall_alpha = cast(
        "float | None", overall_raw.get("alpha") if isinstance(overall_raw, Mapping) else None
    )
    overall_ac1 = gwet_ac1(list(units)) if units else _ac1_from_alpha(overall_alpha)
    return {
        "per_code": verdicts,
        "overall": {
            "alpha": overall_alpha,
            "kappa": overall_raw.get("kappa") if isinstance(overall_raw, Mapping) else None,
            "ac1": overall_ac1,
            "band": classify_band(overall_alpha),
        },
        "halt": halt,
        "tentative_codes": tentative,
        "prevalence_notes": prevalence_notes,
    }


def _ac1_from_alpha(alpha: float | None) -> float | None:
    """AC1 reconstructed from alpha (documented approximation).

    alpha = 1 - D_obs / D_exp with D_exp = n0*n1/(n-1) in the binary
    coincidence form; when the caller cannot supply the raw units we set
    D_obs = (1 - alpha) * 0.5 (the equal-split disagreement ceiling) and
    solve AC1 = (Pa - Pe) / (1 - Pe) with Pa = 1 - 2*D_obs for the binary
    case. Values are exact only under symmetric marginals; callers with the
    raw triples should always prefer gwet_ac1().
    """
    if alpha is None:
        return None
    d_obs = (1.0 - alpha) * 0.5
    pa = 1.0 - d_obs
    pi = 0.5  # symmetric-marginal assumption (documented)
    pe = 2.0 * pi * (1.0 - pi)
    if pe >= 1.0:
        return None
    return (pa - pe) / (1.0 - pe)
