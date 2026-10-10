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
* ``exact_filter_selectivity_threshold`` — U21: when a filtered vector
  query's estimated selectivity (matched/total rows) falls BELOW this
  fraction, the caller runs a filtered EXACT distance scan (no ANN) instead
  of the HNSW probe, because a selective filter can starve an ordered index
  scan of candidates. 0.05-0.1 is a defensible starting point; the U45/U22
  benchmark must tune it — it is documented as a starting point, not a
  proven optimum.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, cast

logger = logging.getLogger(__name__)

DEFAULT_RRF_K = 60
DEFAULT_CANDIDATES_PER_BRANCH = 50
# U21: below 5% estimated selectivity, prefer the filtered exact scan.
DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD = 0.05


RETRIEVAL_CONFIG_DEFAULTS: dict[str, object] = {
    "rrf_k": DEFAULT_RRF_K,
    "candidates_per_branch": DEFAULT_CANDIDATES_PER_BRANCH,
    "exact_filter_selectivity_threshold": DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
}


@dataclass(frozen=True)
class RetrievalSettings:
    """Typed reads for the shared retrieval knobs (U45/U21).

    No ``return_top_k``: the post-fusion return count is owned by the
    caller's mandatory ``k`` field (``ResearchQuery.k`` / ``SearchQuery.k``),
    so a second truncation knob here would be inert.
    """

    rrf_k: int = DEFAULT_RRF_K
    candidates_per_branch: int = DEFAULT_CANDIDATES_PER_BRANCH
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


class SelectivityReader(Protocol):
    """Minimal asyncpg.Connection surface the selectivity probe needs."""

    async def fetchrow(self, sql: str, *args: object) -> object | None:
        """Run one paired-count query; returns the row or None."""
        ...


async def estimate_selectivity(
    conn: SelectivityReader,
    count_sql: str,
    params: list[object],
) -> float | None:
    """Run one paired-count probe (total + FILTER-matched) and return the ratio.

    ``count_sql`` must select ``total`` and ``matched`` (see the
    ``*_COUNT_SQL`` constants at the call sites). Any probe failure returns
    None and the caller fails OPEN to the ANN path — a broken probe must
    never break search; it only costs the U21 optimization.
    """
    try:
        row = await conn.fetchrow(count_sql, *params)
    except Exception as exc:
        logger.warning("Selectivity probe failed; staying on the ANN path: %s", exc)
        return None
    if row is None:
        return None
    record = cast("dict[str, object]", dict(row)) if not isinstance(row, dict) else row
    try:
        return selectivity_ratio(int(record["matched"]), int(record["total"]))
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Selectivity probe returned unusable row: %s", exc)
        return None


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
