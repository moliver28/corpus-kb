"""U30: typed rerank settings read from the ``reranking.*`` config block.

Same house pattern as ``research/search_settings.py`` (U20): frozen dataclass,
module-level defaults, and a ``load_rerank_settings(config)`` reader so the
YAML files, ``config.py`` defaults and this module cannot drift. The block
was renamed from ``search.rerank.*`` in the config-v2 wave; the loader's
alias layer copies legacy values in, so this reader only ever sees the
canonical ``reranking.*`` keys.

The shipped default is ON (the committed upgrade-program posture: the qwen3
rerank stage is enabled but degrades honestly to the un-reranked RRF order
whenever the model or the endpoint is unavailable — reranking must never
become a hard dependency of search, rag/reranker.py degrade contract).
``enabled: false`` opts out entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

DEFAULT_RERANK_ENABLED = True
DEFAULT_RERANK_BACKEND: str | None = None
DEFAULT_RERANK_MODEL = "qwen3-reranker:4b"
DEFAULT_RERANK_BASE_URL = "http://localhost:11434"
DEFAULT_OVER_RETRIEVE_N = 60
DEFAULT_RERANK_CANDIDATES = 100
DEFAULT_RETURN_TOP_K = 20
DEFAULT_MAX_PAIR_TOKENS = 768
DEFAULT_SCORE_FLOOR = 0.15
DEFAULT_CALIBRATION = "minmax"
DEFAULT_RERANK_TIMEOUT_SECONDS = 120.0

CALIBRATION_MODES = frozenset({"minmax", "none"})

RETURN_TOP_K_MIN = 1
RETURN_TOP_K_MAX = 1000


RERANK_CONFIG_DEFAULTS: dict[str, object] = {
    "enabled": DEFAULT_RERANK_ENABLED,
    "backend": DEFAULT_RERANK_BACKEND,
    "model": DEFAULT_RERANK_MODEL,
    "base_url": DEFAULT_RERANK_BASE_URL,
    "over_retrieve_n": DEFAULT_OVER_RETRIEVE_N,
    "candidates": DEFAULT_RERANK_CANDIDATES,
    "return_top_k": DEFAULT_RETURN_TOP_K,
    "max_pair_tokens": DEFAULT_MAX_PAIR_TOKENS,
    "score_floor": DEFAULT_SCORE_FLOOR,
    "calibration": DEFAULT_CALIBRATION,
    "timeout_seconds": DEFAULT_RERANK_TIMEOUT_SECONDS,
}


@dataclass(frozen=True)
class RerankSettings:
    """Typed ``reranking.*`` reads (U30).

    ``candidates`` bounds how many post-fusion results enter the reranker
    (the spec's top 50-100); ``over_retrieve_n`` is the arms' rerank
    pre-fetch width (QueryHandler feeds at most that many fused results);
    ``return_top_k`` IS the default return k (W3): a caller that omits k
    on search/query gets this value, and the catalogue pins the same
    number (lockstep test in tests/test_config_blocks.py).
    ``max_pair_tokens`` truncates the document side of each (query,
    document) pair before scoring; ``score_floor`` drops results below the
    calibrated score; ``calibration`` selects the score post-processing
    (``minmax`` or raw ``none``).
    """

    enabled: bool = DEFAULT_RERANK_ENABLED
    backend: str | None = DEFAULT_RERANK_BACKEND
    model: str = DEFAULT_RERANK_MODEL
    base_url: str = DEFAULT_RERANK_BASE_URL
    over_retrieve_n: int = DEFAULT_OVER_RETRIEVE_N
    candidates: int = DEFAULT_RERANK_CANDIDATES
    return_top_k: int = DEFAULT_RETURN_TOP_K
    max_pair_tokens: int = DEFAULT_MAX_PAIR_TOKENS
    score_floor: float = DEFAULT_SCORE_FLOOR
    calibration: str = DEFAULT_CALIBRATION
    timeout_seconds: float = DEFAULT_RERANK_TIMEOUT_SECONDS


def _return_top_k_error(value: object) -> str | None:
    """Range rule for ``reranking.return_top_k``: a positive int in 1..1000."""
    if isinstance(value, bool) or not isinstance(value, int):
        return f"reranking.return_top_k must be an int; got {value!r}"
    if not RETURN_TOP_K_MIN <= value <= RETURN_TOP_K_MAX:
        return (
            f"reranking.return_top_k must be between {RETURN_TOP_K_MIN} and "
            f"{RETURN_TOP_K_MAX}; got {value}"
        )
    return None


def load_rerank_settings(config: dict[str, object]) -> RerankSettings:
    """Read ``reranking.*`` from a loaded config, defaulting safely.

    Invalid values raise ``ValueError`` — a misconfigured calibration mode
    must fail loudly rather than silently score differently.
    """
    reranking = config.get("reranking", {})
    if not isinstance(reranking, dict):
        return RerankSettings()
    block = cast(dict[str, object], reranking)

    enabled = block.get("enabled", DEFAULT_RERANK_ENABLED)
    if not isinstance(enabled, bool):
        raise ValueError(f"reranking.enabled must be a bool; got {enabled!r}")
    backend_raw = block.get("backend", DEFAULT_RERANK_BACKEND)
    backend = str(backend_raw) if backend_raw is not None else None
    model = str(block.get("model", DEFAULT_RERANK_MODEL))
    base_url = str(block.get("base_url", DEFAULT_RERANK_BASE_URL))
    over_retrieve_n = _positive_int(
        block.get("over_retrieve_n"), DEFAULT_OVER_RETRIEVE_N, "over_retrieve_n"
    )
    candidates = _positive_int(block.get("candidates"), DEFAULT_RERANK_CANDIDATES, "candidates")
    top_k_error = _return_top_k_error(block.get("return_top_k", DEFAULT_RETURN_TOP_K))
    if top_k_error is not None:
        raise ValueError(top_k_error)
    return_top_k = int(block.get("return_top_k", DEFAULT_RETURN_TOP_K))
    max_pair_tokens = _positive_int(
        block.get("max_pair_tokens"), DEFAULT_MAX_PAIR_TOKENS, "max_pair_tokens"
    )
    floor = block.get("score_floor", DEFAULT_SCORE_FLOOR)
    if isinstance(floor, bool) or not isinstance(floor, (int, float)):
        raise ValueError(f"reranking.score_floor must be a number; got {floor!r}")
    calibration = str(block.get("calibration", DEFAULT_CALIBRATION))
    if calibration not in CALIBRATION_MODES:
        modes = ", ".join(sorted(CALIBRATION_MODES))
        raise ValueError(f"reranking.calibration must be one of ({modes}); got {calibration!r}")
    timeout = block.get("timeout_seconds", DEFAULT_RERANK_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError(f"reranking.timeout_seconds must be a number; got {timeout!r}")
    if float(timeout) <= 0:
        raise ValueError(f"reranking.timeout_seconds must be positive; got {timeout!r}")
    return RerankSettings(
        enabled=enabled,
        backend=backend,
        model=model,
        base_url=base_url,
        over_retrieve_n=over_retrieve_n,
        candidates=candidates,
        return_top_k=return_top_k,
        max_pair_tokens=max_pair_tokens,
        score_floor=float(floor),
        calibration=calibration,
        timeout_seconds=float(timeout),
    )


def _positive_int(value: object, default: int, name: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"reranking.{name} must be an int; got {value!r}")
    if value <= 0:
        raise ValueError(f"reranking.{name} must be a positive int; got {value}")
    return value
