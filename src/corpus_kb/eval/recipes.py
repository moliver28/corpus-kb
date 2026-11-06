"""Query-time recipe overrides for calibration sweeps (recipe PR-1).

A recipe is a plain dict of config dot-paths and values, applied to a deep
copy of the sweep's config at RANK time. No ingest-time knobs exist here:
every candidate ranks the SAME ingested corpus, so a sweep measures only
what the query-time seam can change (fusion constants, per-arm candidate
depths, reranker on/off). ``retrieval.*`` paths reach
``research.retrieval.ranked_children``; ``reranking.enabled`` toggles the
reranker argument of the same seam. ``reranking.over_retrieve_n`` and
``reranking.score_floor`` are accepted for config consistency; the rank
seam itself does not read them. ``analysis.routing.*`` paths serve the
routing calibration sweep (``eval.calibrate.run_routing_calibration``);
the rank seam never reads them.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from corpus_kb.errors import ValidationError

__all__ = ["ALLOWED_OVERRIDES", "MAX_CANDIDATES", "apply_overrides", "validate_candidates"]

ALLOWED_OVERRIDES: frozenset[str] = frozenset(
    {
        "retrieval.rrf_k",
        "retrieval.vector_candidates",
        "retrieval.lexical_candidates",
        "reranking.enabled",
        "reranking.over_retrieve_n",
        "reranking.score_floor",
        "analysis.routing.confidence_threshold",
        "analysis.routing.margin",
    }
)

MAX_CANDIDATES = 8


def _reject_unknown_keys(name: str, overrides: Mapping[str, object]) -> None:
    unknown = sorted(set(overrides) - ALLOWED_OVERRIDES)
    if unknown:
        examples = ", ".join(sorted(ALLOWED_OVERRIDES)[:3])
        raise ValidationError(
            f"Candidate {name!r} sets unknown recipe keys: {', '.join(unknown)}.",
            remediation=f"Use query-time knobs only, e.g. {examples}.",
        )


def validate_candidates(candidates: Mapping[str, Mapping[str, object]]) -> None:
    """Reject a family that is too large or carries non-query-time keys."""
    if len(candidates) > MAX_CANDIDATES:
        raise ValidationError(
            f"A sweep allows at most {MAX_CANDIDATES} candidates; got {len(candidates)}.",
            remediation=f"Trim the candidate list to {MAX_CANDIDATES} or fewer.",
        )
    for name, overrides in candidates.items():
        _reject_unknown_keys(name, overrides)


def apply_overrides(
    base_config: Mapping[str, object], overrides: Mapping[str, object]
) -> dict[str, object]:
    """Deep-copy ``base_config`` and set each dot-path key to its value.

    A dot path may nest (``analysis.routing.margin``): the walk creates
    missing intermediate blocks and never touches sibling keys.
    """
    _reject_unknown_keys("recipe", overrides)
    merged: dict[str, object] = deepcopy(dict(base_config))
    for dot_path, value in overrides.items():
        parts = dot_path.split(".")
        block = merged
        for part in parts[:-1]:
            nested = block.get(part)
            child: dict[str, object] = dict(nested) if isinstance(nested, Mapping) else {}
            block[part] = child
            block = child
        block[parts[-1]] = value
    return merged
