"""U45/U21: typed retrieval settings read from the ``search.*`` config block.

The house pattern is ``research/search_settings.py`` (U20): a frozen dataclass,
module-level defaults, and a ``load_*_settings(config)`` reader so the YAML
files, ``config.py`` defaults and this module cannot drift. The orchestrator
wires these keys into both YAML files at the phase checkpoint; until then the
module defaults ARE the behavior (missing keys fall back, never raise).

Keys (all under ``search``):

* ``rrf_k`` — reciprocal-rank-fusion constant (was hardcoded 60 in two call
  sites; U45 wires the read). Σ 1/(k + rank) over both arms.
* ``candidates_per_branch`` — depth each arm retrieves before fusion
  (spec v8 U45 default 50; call sites take max(depth, rerank floor)).
* ``return_top_k`` — post-fusion return count when the caller passes
  ``k=None``-style defaults (today callers pass explicit k; the read keeps
  the knob honest and consumed).
* ``exact_filter_selectivity_threshold`` — U21: when a filtered vector
  query's estimated selectivity (matched/total rows) falls BELOW this
  fraction, the caller runs a filtered EXACT distance scan (no ANN) instead
  of the HNSW probe, because a selective filter can starve an ordered index
  scan of candidates. 0.05-0.1 is a defensible starting point; the U45/U22
  benchmark must tune it — it is documented as a starting point, not a
  proven optimum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

DEFAULT_RRF_K = 60
DEFAULT_CANDIDATES_PER_BRANCH = 50
DEFAULT_RETURN_TOP_K = 10
# U21: below 5% estimated selectivity, prefer the filtered exact scan.
DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD = 0.05


RETRIEVAL_CONFIG_DEFAULTS: dict[str, object] = {
    "rrf_k": DEFAULT_RRF_K,
    "candidates_per_branch": DEFAULT_CANDIDATES_PER_BRANCH,
    "return_top_k": DEFAULT_RETURN_TOP_K,
    "exact_filter_selectivity_threshold": DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
}


@dataclass(frozen=True)
class RetrievalSettings:
    """Typed reads for the shared retrieval knobs (U45/U21)."""

    rrf_k: int = DEFAULT_RRF_K
    candidates_per_branch: int = DEFAULT_CANDIDATES_PER_BRANCH
    return_top_k: int = DEFAULT_RETURN_TOP_K
    exact_filter_selectivity_threshold: float = DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD


def _positive_int(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"search.{name} must be an int; got {value!r}")
    if value <= 0:
        raise ValueError(f"search.{name} must be a positive int; got {value}")
    return value


def _fraction(value: object, default: float, name: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"search.{name} must be a number in [0, 1); got {value!r}")
    coerced = float(value)
    if not 0.0 <= coerced < 1.0:
        raise ValueError(f"search.{name} must be a number in [0, 1); got {coerced}")
    return coerced


def load_retrieval_settings(config: dict[str, object]) -> RetrievalSettings:
    """Read the ``search.*`` retrieval knobs, defaulting to the module constants.

    Invalid values raise ``ValueError`` — a misconfigured fusion constant must
    fail loudly rather than silently change retrieval behavior.
    """
    search = config.get("search", {})
    block: dict[str, object] = search if isinstance(search, dict) else {}
    block = cast(dict[str, object], block)
    return RetrievalSettings(
        rrf_k=_positive_int(block.get("rrf_k"), DEFAULT_RRF_K, "rrf_k"),
        candidates_per_branch=_positive_int(
            block.get("candidates_per_branch"),
            DEFAULT_CANDIDATES_PER_BRANCH,
            "candidates_per_branch",
        ),
        return_top_k=_positive_int(block.get("return_top_k"), DEFAULT_RETURN_TOP_K, "return_top_k"),
        exact_filter_selectivity_threshold=_fraction(
            block.get("exact_filter_selectivity_threshold"),
            DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
            "exact_filter_selectivity_threshold",
        ),
    )


def selectivity_ratio(matched: int, total: int) -> float:
    """matched/total clamped to [0, 1]; an empty scope is maximally selective."""
    if total <= 0:
        return 0.0
    return min(1.0, max(0.0, matched / total))


def should_use_exact_scan(
    has_filters: bool,
    selectivity: float | None,
    threshold: float,
) -> bool:
    """U21 arm-selection policy.

    Exact scan only for FILTERED queries whose estimated selectivity falls
    below the threshold. Unfiltered queries never take the exact path (a
    full-table exact distance scan defeats the index for no correctness
    gain — selectivity 1.0 is above any sane threshold anyway).
    ``selectivity=None`` (probe failed) fails OPEN to the ANN path: the ANN
    answer is approximate, the exact answer is merely slow.
    """
    if not has_filters:
        return False
    if selectivity is None:
        return False
    return selectivity < threshold
