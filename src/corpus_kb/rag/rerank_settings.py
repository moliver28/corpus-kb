"""U30: typed rerank settings read from the ``search.rerank.*`` config block.

Same house pattern as ``research/search_settings.py`` (U20): frozen dataclass,
module-level defaults, and a ``load_rerank_settings(config)`` reader so the
YAML files, ``config.py`` defaults and this module cannot drift. The
orchestrator wires the block into both YAML files at the phase checkpoint;
until then these defaults ARE the behavior for keys the config omits.

The shipped default is OFF (spec v6 U30: "config-selected provider with
``off`` default"): reranking is a strict quality add-on that must never
become a hard dependency of search, and enabling it before the U45/U22
benchmark proves an nDCG/recall gain inside the p95 budget would flip a
retrieval default on vibes. ``enabled: true`` in config opts in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

DEFAULT_RERANK_ENABLED = False
DEFAULT_RERANK_MODEL = "qwen3-reranker:4b"
DEFAULT_RERANK_BASE_URL = "http://localhost:11434"
DEFAULT_RERANK_CANDIDATES = 100
DEFAULT_MAX_PAIR_TOKENS = 768
DEFAULT_SCORE_FLOOR = 0.15
DEFAULT_CALIBRATION = "minmax"
DEFAULT_RERANK_TIMEOUT_SECONDS = 120.0

CALIBRATION_MODES = frozenset({"minmax", "none"})


RERANK_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": DEFAULT_RERANK_ENABLED,
    "model": DEFAULT_RERANK_MODEL,
    "base_url": DEFAULT_RERANK_BASE_URL,
    "candidates": DEFAULT_RERANK_CANDIDATES,
    "max_pair_tokens": DEFAULT_MAX_PAIR_TOKENS,
    "score_floor": DEFAULT_SCORE_FLOOR,
    "calibration": DEFAULT_CALIBRATION,
    "timeout_seconds": DEFAULT_RERANK_TIMEOUT_SECONDS,
}


@dataclass(frozen=True)
class RerankSettings:
    """Typed ``search.rerank.*`` reads (U30).

    ``candidates`` bounds how many post-fusion results enter the reranker
    (the spec's top 50-100); ``max_pair_tokens`` truncates the document side
    of each (query, document) pair before scoring; ``score_floor`` drops
    results below the calibrated score; ``calibration`` selects the score
    post-processing (``minmax`` or raw ``none``).
    """

    enabled: bool = DEFAULT_RERANK_ENABLED
    model: str = DEFAULT_RERANK_MODEL
    base_url: str = DEFAULT_RERANK_BASE_URL
    candidates: int = DEFAULT_RERANK_CANDIDATES
    max_pair_tokens: int = DEFAULT_MAX_PAIR_TOKENS
    score_floor: float = DEFAULT_SCORE_FLOOR
    calibration: str = DEFAULT_CALIBRATION
    timeout_seconds: float = DEFAULT_RERANK_TIMEOUT_SECONDS


def load_rerank_settings(config: dict[str, object]) -> RerankSettings:
    """Read ``search.rerank.*`` from a loaded config, defaulting safely.

    Invalid values raise ``ValueError`` — a misconfigured calibration mode
    must fail loudly rather than silently score differently.
    """
    search = config.get("search", {})
    if not isinstance(search, dict):
        return RerankSettings()
    rerank = cast(dict[str, object], search).get("rerank", {})
    if not isinstance(rerank, dict):
        return RerankSettings()
    block = cast(dict[str, object], rerank)

    enabled = block.get("enabled", DEFAULT_RERANK_ENABLED)
    if not isinstance(enabled, bool):
        raise ValueError(f"search.rerank.enabled must be a bool; got {enabled!r}")
    model = str(block.get("model", DEFAULT_RERANK_MODEL))
    base_url = str(block.get("base_url", DEFAULT_RERANK_BASE_URL))
    candidates = _positive_int(
        block.get("candidates"), DEFAULT_RERANK_CANDIDATES, "candidates"
    )
    max_pair_tokens = _positive_int(
        block.get("max_pair_tokens"), DEFAULT_MAX_PAIR_TOKENS, "max_pair_tokens"
    )
    floor = block.get("score_floor", DEFAULT_SCORE_FLOOR)
    if isinstance(floor, bool) or not isinstance(floor, (int, float)):
        raise ValueError(f"search.rerank.score_floor must be a number; got {floor!r}")
    calibration = str(block.get("calibration", DEFAULT_CALIBRATION))
    if calibration not in CALIBRATION_MODES:
        modes = ", ".join(sorted(CALIBRATION_MODES))
        raise ValueError(
            f"search.rerank.calibration must be one of ({modes}); got {calibration!r}"
        )
    timeout = block.get("timeout_seconds", DEFAULT_RERANK_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError(
            f"search.rerank.timeout_seconds must be a number; got {timeout!r}"
        )
    if float(timeout) <= 0:
        raise ValueError(
            f"search.rerank.timeout_seconds must be positive; got {timeout!r}"
        )
    return RerankSettings(
        enabled=enabled,
        model=model,
        base_url=base_url,
        candidates=candidates,
        max_pair_tokens=max_pair_tokens,
        score_floor=float(floor),
        calibration=calibration,
        timeout_seconds=float(timeout),
    )


def _positive_int(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"search.rerank.{name} must be an int; got {value!r}")
    if value <= 0:
        raise ValueError(f"search.rerank.{name} must be a positive int; got {value}")
    return value
