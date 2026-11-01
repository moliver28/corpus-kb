"""U45/U21: typed retrieval settings read from the ``retrieval.*`` config block.

The house pattern is ``research/search_settings.py`` (U20): a frozen dataclass,
module-level defaults, and a ``load_*_settings(config)`` reader so the YAML
files, ``config.py`` defaults and this module cannot drift. The block was
renamed from ``search.*`` in the config-v2 wave; the loader's alias layer
copies legacy values in, so this reader only ever sees canonical keys.

Keys (under ``retrieval``):

* ``mode`` — retrieval strategy selector. Only ``hybrid_rrf`` exists today;
  any other value raises (the key exists so future modes are additive).
* ``rrf_k`` — reciprocal-rank-fusion constant. Σ 1/(k + rank) over both arms.
* ``vector_candidates`` / ``lexical_candidates`` — LIVE per-arm depths each
  arm retrieves before fusion (config-v2 wiring of the former benchmark-only
  knob); call sites take max(candidates, k * 2) so small values never
  under-fetch below k.
* ``expand_to_parent`` — post-fusion small-to-big parent expansion (moved
  from ``ingest.hierarchy``; fail-open).
* ``exclude_degraded`` — candidate CTEs drop rows flagged degraded.
* ``qa_context_in_fts`` — transcript Q/A text joins the shared FTS index.
* ``exact_filter_selectivity_threshold`` — when a filtered vector query's
  estimated selectivity falls BELOW this fraction, the caller runs a
  filtered EXACT distance scan (no ANN) instead of the HNSW probe, because
  a selective filter can starve an ordered index scan of candidates.
  0.05-0.1 is a defensible starting point; the U45/U22 benchmark must tune
  it — it is documented as a starting point, not a proven optimum.
* ``candidate_multiplier`` — matryoshka fan-out (candidates = k * multiplier).
* ``matryoshka_enabled`` / ``matryoshka_dim`` — two-stage arm gate + slice.
* ``self_query`` — predicate parser block (enabled/model/base_url/timeout).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from corpus_kb.dimensions import RESEARCH_EMBEDDING_DIMENSIONS

logger = logging.getLogger(__name__)

SUPPORTED_MODES = frozenset({"hybrid_rrf"})

DEFAULT_MODE = "hybrid_rrf"
DEFAULT_RRF_K = 60
DEFAULT_VECTOR_CANDIDATES = 150
DEFAULT_LEXICAL_CANDIDATES = 150
DEFAULT_EXPAND_TO_PARENT = True
DEFAULT_EXCLUDE_DEGRADED = True
DEFAULT_QA_CONTEXT_IN_FTS = False
# U21: below 5% estimated selectivity, prefer the filtered exact scan.
DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD = 0.05
DEFAULT_CANDIDATE_MULTIPLIER = 8
DEFAULT_MATRYOSHKA_ENABLED = False
DEFAULT_MATRYOSHKA_DIM = RESEARCH_EMBEDDING_DIMENSIONS
DEFAULT_SELF_QUERY: dict[str, Any] = {
    "enabled": True,
    "model": "qwen3:4b",
    "base_url": "http://localhost:11434",
    "timeout_seconds": 8,
}

RETRIEVAL_CONFIG_DEFAULTS: dict[str, Any] = {
    "mode": DEFAULT_MODE,
    "vector_candidates": DEFAULT_VECTOR_CANDIDATES,
    "lexical_candidates": DEFAULT_LEXICAL_CANDIDATES,
    "rrf_k": DEFAULT_RRF_K,
    "expand_to_parent": DEFAULT_EXPAND_TO_PARENT,
    "exclude_degraded": DEFAULT_EXCLUDE_DEGRADED,
    "qa_context_in_fts": DEFAULT_QA_CONTEXT_IN_FTS,
    "exact_filter_selectivity_threshold": DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
    "candidate_multiplier": DEFAULT_CANDIDATE_MULTIPLIER,
    "self_query": dict(DEFAULT_SELF_QUERY),
    "matryoshka_enabled": DEFAULT_MATRYOSHKA_ENABLED,
    "matryoshka_dim": DEFAULT_MATRYOSHKA_DIM,
}


def mode_error(mode: object) -> str | None:
    """The refusal message for an unsupported ``retrieval.mode``, or None."""
    if isinstance(mode, str) and mode in SUPPORTED_MODES:
        return None
    return (
        f"retrieval.mode {mode!r} is not supported. The only supported mode is "
        "'hybrid_rrf' (hybrid retrieval with RRF fusion). The key exists so "
        "future modes are additive; set retrieval.mode: hybrid_rrf."
    )


@dataclass(frozen=True)
class RetrievalSettings:
    """Typed reads for the ``retrieval.*`` knobs (U45/U21).

    No ``return_top_k``: the post-fusion return count is owned by the
    caller's mandatory ``k`` field (``ResearchQuery.k`` / ``SearchQuery.k``),
    so a second truncation knob here would be inert. (``reranking.
    return_top_k`` is the rerank-side default k, accepted in the config-v2
    wave and wired to the catalogue contract in wave 3.)
    """

    mode: str = DEFAULT_MODE
    rrf_k: int = DEFAULT_RRF_K
    vector_candidates: int = DEFAULT_VECTOR_CANDIDATES
    lexical_candidates: int = DEFAULT_LEXICAL_CANDIDATES
    expand_to_parent: bool = DEFAULT_EXPAND_TO_PARENT
    exclude_degraded: bool = DEFAULT_EXCLUDE_DEGRADED
    qa_context_in_fts: bool = DEFAULT_QA_CONTEXT_IN_FTS
    exact_filter_selectivity_threshold: float = DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD
    candidate_multiplier: int = DEFAULT_CANDIDATE_MULTIPLIER
    matryoshka_enabled: bool = DEFAULT_MATRYOSHKA_ENABLED
    matryoshka_dim: int = DEFAULT_MATRYOSHKA_DIM
    self_query: dict[str, Any] = field(default_factory=dict)


def _positive_int(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"retrieval.{name} must be an int; got {value!r}")
    if value <= 0:
        raise ValueError(f"retrieval.{name} must be a positive int; got {value}")
    return value


def _flag(value: object, default: bool, name: str) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"retrieval.{name} must be a bool; got {value!r}")
    return value


def _fraction(value: object, default: float, name: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"retrieval.{name} must be a number in [0, 1); got {value!r}")
    coerced = float(value)
    if not 0.0 <= coerced < 1.0:
        raise ValueError(f"retrieval.{name} must be a number in [0, 1); got {coerced}")
    return coerced


def load_retrieval_settings(config: dict[str, Any]) -> RetrievalSettings:
    """Read the ``retrieval.*`` knobs, defaulting to the module constants.

    Invalid values raise ``ValueError`` — a misconfigured fusion constant or
    an unsupported mode must fail loudly rather than silently change
    retrieval behavior.
    """
    retrieval = config.get("retrieval", {})
    block: dict[str, Any] = retrieval if isinstance(retrieval, dict) else {}
    block = cast(dict[str, Any], block)

    mode = str(block.get("mode", DEFAULT_MODE))
    error = mode_error(mode)
    if error is not None:
        raise ValueError(error)

    self_query_raw = block.get("self_query")
    self_query: dict[str, Any] = dict(DEFAULT_SELF_QUERY)
    if isinstance(self_query_raw, dict):
        self_query.update(cast(dict[str, Any], self_query_raw))

    return RetrievalSettings(
        mode=mode,
        rrf_k=_positive_int(block.get("rrf_k"), DEFAULT_RRF_K, "rrf_k"),
        vector_candidates=_positive_int(
            block.get("vector_candidates"), DEFAULT_VECTOR_CANDIDATES, "vector_candidates"
        ),
        lexical_candidates=_positive_int(
            block.get("lexical_candidates"), DEFAULT_LEXICAL_CANDIDATES, "lexical_candidates"
        ),
        expand_to_parent=_flag(
            block.get("expand_to_parent"), DEFAULT_EXPAND_TO_PARENT, "expand_to_parent"
        ),
        exclude_degraded=_flag(
            block.get("exclude_degraded"), DEFAULT_EXCLUDE_DEGRADED, "exclude_degraded"
        ),
        qa_context_in_fts=_flag(
            block.get("qa_context_in_fts"), DEFAULT_QA_CONTEXT_IN_FTS, "qa_context_in_fts"
        ),
        exact_filter_selectivity_threshold=_fraction(
            block.get("exact_filter_selectivity_threshold"),
            DEFAULT_EXACT_FILTER_SELECTIVITY_THRESHOLD,
            "exact_filter_selectivity_threshold",
        ),
        candidate_multiplier=_positive_int(
            block.get("candidate_multiplier"), DEFAULT_CANDIDATE_MULTIPLIER, "candidate_multiplier"
        ),
        matryoshka_enabled=bool(block.get("matryoshka_enabled", DEFAULT_MATRYOSHKA_ENABLED)),
        matryoshka_dim=_positive_int(
            block.get("matryoshka_dim"), DEFAULT_MATRYOSHKA_DIM, "matryoshka_dim"
        ),
        self_query=self_query,
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
    record = cast("Mapping[str, Any]", row)
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
