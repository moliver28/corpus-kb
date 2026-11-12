"""Issue #136: typed settings for the ``retrieval.multi_query`` block.

The house pattern is ``research/search_settings.py`` (U20): a frozen
dataclass, module-level defaults, and a ``load_*_settings(config)`` reader
so the YAML files, ``config.py`` defaults and this module cannot drift.

Multi-query rewriting is query-side diversity for hybrid search (issue
#136, ADOPT verdict from the #133 scan): when enabled, the handler rewrites
the query into ``variants`` diverse rewrites with one LLM call, retrieves
for the original query AND every rewrite, and fuses all ranked lists with
the existing RRF fusion. Default OFF — evidence shows query expansion gives
limited benefit for precise numerical queries, so the corpus owner opts in.

Keys (under ``retrieval.multi_query``):

* ``enabled`` — the master gate. False keeps search byte-identical: no
  LLM call, no extra legs, no envelope key.
* ``variants`` — how many rewrites the rewriter emits (1-10). Fusion runs
  (variants + 1) x 2 ranked lists: the original query pair plus one pair
  per rewrite.
* ``model`` / ``base_url`` / ``timeout_seconds`` — the rewriter chat model.
  Same defaults as ``retrieval.self_query``; a small local chat model is
  enough. The rewriter fails open: no reachable model means the
  single-query path with a degraded note, never a failed search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

DEFAULT_MULTI_QUERY_ENABLED = False
DEFAULT_VARIANTS = 3
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 8.0

MIN_VARIANTS = 1
MAX_VARIANTS = 10

DEFAULT_MULTI_QUERY: dict[str, Any] = {
    "enabled": DEFAULT_MULTI_QUERY_ENABLED,
    "variants": DEFAULT_VARIANTS,
    "model": DEFAULT_MODEL,
    "base_url": DEFAULT_BASE_URL,
    "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
}


@dataclass(frozen=True)
class MultiQuerySettings:
    """Typed reads for the ``retrieval.multi_query.*`` knobs (issue #136)."""

    enabled: bool = DEFAULT_MULTI_QUERY_ENABLED
    variants: int = DEFAULT_VARIANTS
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS


def variants_error(value: object) -> str | None:
    """The refusal message for an out-of-range ``variants``, or None."""
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and MIN_VARIANTS <= value <= MAX_VARIANTS
    ):
        return None
    return (
        f"retrieval.multi_query.variants {value!r} is not supported. "
        f"Set an int between {MIN_VARIANTS} and {MAX_VARIANTS}."
    )


def load_multi_query_settings(config: dict[str, Any]) -> MultiQuerySettings:
    """Read the ``retrieval.multi_query`` block, defaulting to the constants.

    Invalid values raise ``ValueError`` — a misconfigured gate must fail
    loudly rather than silently change retrieval behavior (the
    ``load_retrieval_settings`` contract).
    """
    retrieval = config.get("retrieval", {})
    block_raw = (retrieval if isinstance(retrieval, dict) else {}).get("multi_query")
    block: dict[str, Any] = dict(DEFAULT_MULTI_QUERY)
    if isinstance(block_raw, dict):
        block.update(cast(dict[str, Any], block_raw))

    enabled = block.get("enabled", DEFAULT_MULTI_QUERY_ENABLED)
    if not isinstance(enabled, bool):
        raise ValueError(f"retrieval.multi_query.enabled must be a bool; got {enabled!r}")

    variants = block.get("variants", DEFAULT_VARIANTS)
    error = variants_error(variants)
    if error is not None:
        raise ValueError(error)

    timeout = block.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError(
            f"retrieval.multi_query.timeout_seconds must be a positive number; got {timeout!r}"
        )

    return MultiQuerySettings(
        enabled=enabled,
        variants=int(variants),
        model=str(block.get("model", DEFAULT_MODEL)),
        base_url=str(block.get("base_url", DEFAULT_BASE_URL)),
        timeout_seconds=float(timeout),
    )
