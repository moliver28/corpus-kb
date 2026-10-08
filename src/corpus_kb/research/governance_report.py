"""Pinned governance report schema (todo 17, r9/r10/r12).

REPORT SCHEMA PIN: every REQUIRED field below is asserted present by the
fixture run (tests/test_research_report_fixture.py), so the
``corpus-kb research report`` surface cannot rot into a stub. Both
presentation levels are transforms over the ONE computation: ``expert`` is
the schema unchanged; ``novice`` adds traffic lights, plain-language copy
(from guide_copy — the sole prose source), and a NEXT-ACTION list derived
from existing flags.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from corpus_kb.research import guide_copy

SCHEMA_VERSION = "1.0"
TRAFFIC_GREEN = "green"
TRAFFIC_YELLOW = "yellow"
TRAFFIC_RED = "red"


@dataclass(frozen=True)
class ResearchReport:
    """The pinned required-field set (r9, extended r10; fixture-asserted)."""

    schema_version: str
    generated_at: str
    level: str
    tenant_id: str
    codebook_version_id: str
    codebook_label: str
    # §14 checkpoint numbers
    isr_pooled: float
    isr_by_source_type: dict[str, float]
    run_stop: dict[str, object]
    coverage_explicit: float
    coverage_qdep: float
    gray_zone_share: float
    tier3_disagreement_rate: float
    link_accuracy: dict[str, object]
    review_backlog: dict[str, int]
    kappa_alpha: dict[str, object]  # dual IRR: kappa + alpha + AC1 + bands
    # r7 exhaustiveness sections
    residual: dict[str, object]  # tau_res per type + R + P10/P50/P90 per type
    coverage_curve: dict[str, object]  # R slope + new-codes slope + plateau
    deductive_coverage_note: str  # r8: §8 coverage reported separately
    missing_codes: dict[str, object]  # candidate clusters (low-r low-entropy)
    cluster_stability: dict[str, object]  # bootstrap ARI + threshold verdict
    overlap: dict[str, object]  # matrix + flagged pairs + confusion + silhouette
    keywords: dict[str, object]  # per-code lists + hit_location + conflicts
    # r10 blocks
    g3_audit: dict[str, object]  # AUROC/AURC/acc@coverage/ECE/LLM-vs-human alpha
    conformal: dict[str, object]  # set-size distribution + coverage
    # reproducibility
    run_manifest: dict[str, object]
    codebook_diff: dict[str, object]
    novice_view: dict[str, object] = field(default_factory=dict)


REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "isr_pooled",
        "isr_by_source_type",
        "run_stop",
        "coverage_explicit",
        "coverage_qdep",
        "gray_zone_share",
        "tier3_disagreement_rate",
        "link_accuracy",
        "review_backlog",
        "kappa_alpha",
        "residual",
        "coverage_curve",
        "missing_codes",
        "cluster_stability",
        "overlap",
        "keywords",
        "g3_audit",
        "conformal",
        "run_manifest",
        "codebook_diff",
    }
)


def validate_required(payload: dict[str, object]) -> list[str]:
    """Names of REQUIRED report fields missing from a serialized report."""
    missing = [name for name in sorted(REQUIRED_FIELDS) if payload.get(name) is None]
    if payload.get("schema_version") != SCHEMA_VERSION:
        missing.append("schema_version")
    return missing


def to_dict(report: ResearchReport) -> dict[str, object]:
    """Expert-level JSON payload (the schema-pinned artifact, unchanged)."""
    return asdict(report)


def _mapping(value: object) -> dict[str, object]:
    """Typed accessor for nested report dicts (no Any, no ignores)."""
    if isinstance(value, dict):
        return value
    return {}


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def novice_view(report: ResearchReport) -> dict[str, object]:
    """Traffic lights + plain language + next actions (both levels over ONE
    computation; every string sourced from guide_copy)."""
    residual = _mapping(report.residual)
    pooled_r = _float(_mapping(residual.get("pooled")).get("R"))
    if pooled_r <= 0.05:
        residual_light, residual_copy = TRAFFIC_GREEN, guide_copy.RESIDUAL_LIGHT_GREEN
    elif pooled_r <= 0.20:
        residual_light, residual_copy = TRAFFIC_YELLOW, guide_copy.RESIDUAL_LIGHT_YELLOW
    else:
        residual_light, residual_copy = TRAFFIC_RED, guide_copy.RESIDUAL_LIGHT_RED

    isr = report.isr_pooled
    isr_light = TRAFFIC_GREEN if isr <= 0.05 else TRAFFIC_YELLOW
    isr_copy = guide_copy.ISR_LIGHT_GREEN if isr <= 0.05 else guide_copy.ISR_LIGHT_YELLOW

    irr = _mapping(report.kappa_alpha)
    halt = bool(irr.get("halt", False))
    tentative = bool(irr.get("tentative_codes"))
    if halt:
        irr_light, irr_copy = TRAFFIC_RED, guide_copy.IRR_LIGHT_RED
    elif tentative:
        irr_light, irr_copy = TRAFFIC_YELLOW, guide_copy.IRR_LIGHT_YELLOW
    else:
        irr_light, irr_copy = TRAFFIC_GREEN, guide_copy.IRR_LIGHT_GREEN

    stability = _mapping(report.cluster_stability)
    stable = stability.get("stable")

    overlap_flags = _mapping(report.overlap).get("flagged_pairs", [])

    next_actions: list[str] = []
    if _mapping(report.missing_codes).get("candidates"):
        next_actions.append(guide_copy.RESIDUAL_ACTION)
    if overlap_flags:
        next_actions.append(guide_copy.OVERLAP_ACTION)
    if _mapping(report.keywords).get("conflicts"):
        next_actions.append(guide_copy.CONFLICT_ACTION)
    if tentative:
        next_actions.append(guide_copy.IRR_ACTION)
    if _int(_mapping(report.review_backlog).get("assignments")):
        next_actions.append(guide_copy.BACKLOG_ACTION)
    if not next_actions:
        next_actions.append(guide_copy.STABILITY_ACTION)

    overlap_red = bool(overlap_flags)
    plain_language = {
        "isr": guide_copy.ISR_PLAIN,
        "coverage": guide_copy.COVERAGE_PLAIN,
        "residual": guide_copy.RESIDUAL_PLAIN,
        "tau_res": guide_copy.TAU_RES_PLAIN,
        "stability": guide_copy.STABILITY_PLAIN,
        "overlap": guide_copy.OVERLAP_PLAIN,
        "conflicts": guide_copy.CONFLICT_PLAIN,
        "keywords": guide_copy.KEYWORDS_PLAIN,
        "keywords_provisional": guide_copy.KEYWORDS_PROVISIONAL,
        "irr": guide_copy.IRR_PLAIN,
        "g3": guide_copy.G3_PLAIN,
        "conformal": guide_copy.CONFORMAL_PLAIN,
        "manifest": guide_copy.MANIFEST_PLAIN,
        "missing_codes": guide_copy.MISSING_CODES_PLAIN,
        "backlog": guide_copy.BACKLOG_PLAIN,
    }
    doc_links = {
        section: f"{guide_copy.REPORT_DOC_PATH}#{guide_copy.REPORT_DOC_ANCHORS[section]}"
        for section in plain_language
        if section in guide_copy.REPORT_DOC_ANCHORS
    }
    return {
        "traffic_lights": {
            "isr": {"level": isr_light, "message": isr_copy},
            "residual": {"level": residual_light, "message": residual_copy},
            "irr": {"level": irr_light, "message": irr_copy},
            "overlap": {
                "level": TRAFFIC_RED if overlap_red else TRAFFIC_GREEN,
                "message": (
                    guide_copy.OVERLAP_LIGHT_RED if overlap_red else guide_copy.OVERLAP_LIGHT_GREEN
                ),
            },
            "stability": {
                "level": TRAFFIC_GREEN if stable is True else TRAFFIC_YELLOW,
                "message": (
                    guide_copy.STABILITY_LIGHT_GREEN
                    if stable is True
                    else guide_copy.STABILITY_LIGHT_YELLOW
                ),
            },
        },
        "plain_language": plain_language,
        "doc_links": doc_links,
        "next_actions": next_actions,
        "footer": guide_copy.NEXT_ACTION_FOOTER,
    }


def with_level(report: ResearchReport, level: str) -> ResearchReport:
    """Attach the novice view (the expert artifact itself never changes)."""
    if level == guide_copy.LEVEL_NOVICE:
        return ResearchReport(
            **{**asdict(report), "level": level, "novice_view": novice_view(report)}
        )
    return report
