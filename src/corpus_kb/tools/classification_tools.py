"""MCP/HTTP/socket/CLI handlers for the classification ops (analysis W0).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern:

  * :func:`classification_build` — the offline few-shot run: fit the
    configured tier (head on stored embeddings, or the opt-in SetFit
    body) against a seed label set, write calibrated per-chunk
    predictions (wholesale write per set), route low confidence to the
    review flag.
  * :func:`classification_list` — the tenant's built predictions.

Wave 0 is the registry-first skeleton: the catalogue rows and the honest
envelopes exist, the engines land later. Every handler answers the house
envelopes on every state — the dormant default
(``analysis.classification.enabled: false``) is a no-op envelope naming
the flag; an enabled flag on the skeleton is ``unavailable`` (the engine
is not built yet), never a crash or fake data. When the engine lands,
the list op drops the flag gate (reads are never gated — the
``topics_list`` rule) and keeps the pre-migration ``unavailable`` check.

Envelope: ``{status, prediction_count, class_count, review_count,
params}`` for the build, ``{status, prediction_count, predictions}``
for the list — guaranteed on every outcome, so the parity gate holds on
the disabled path too.
"""

from __future__ import annotations

import asyncpg

from ..analysis.settings import load_analysis_settings
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["classification_build", "classification_list"]

# Read/list bounds (the ingest_status convention: bounded, honest windows).
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

_DISABLED_REASON = "analysis.classification.enabled is false in the loaded config."
_DISABLED_REMEDY = "Set analysis.classification.enabled: true, then retry."
_UNAVAILABLE_REASON = (
    "the classification engine is not built yet (the analysis program ships the registry first)"
)
_UNAVAILABLE_REMEDY = (
    "keep analysis.classification.enabled: false until the classification engine lands"
)


def _disabled_build() -> dict[str, object]:
    """The honest no-op envelope (build keys) for the dormant default."""
    return {
        "status": "disabled",
        "reason": _DISABLED_REASON,
        "remedy": _DISABLED_REMEDY,
        "prediction_count": 0,
        "class_count": 0,
        "review_count": 0,
        "params": {},
    }


def _unavailable_build() -> dict[str, object]:
    """The honest wave-0 envelope (build keys): flag on, engine absent."""
    return {
        "status": "unavailable",
        "reason": _UNAVAILABLE_REASON,
        "remedy": _UNAVAILABLE_REMEDY,
        "prediction_count": 0,
        "class_count": 0,
        "review_count": 0,
        "params": {},
    }


def _disabled_list() -> dict[str, object]:
    """The honest no-op envelope (list keys) for the dormant default."""
    return {
        "status": "disabled",
        "reason": _DISABLED_REASON,
        "remedy": _DISABLED_REMEDY,
        "prediction_count": 0,
        "predictions": [],
    }


def _unavailable_list() -> dict[str, object]:
    """The honest wave-0 envelope (list keys): flag on, engine absent."""
    return {
        "status": "unavailable",
        "reason": _UNAVAILABLE_REASON,
        "remedy": _UNAVAILABLE_REMEDY,
        "prediction_count": 0,
        "predictions": [],
    }


def _classification_on(config: dict[str, object] | None) -> bool:
    """True when ``analysis.classification.enabled`` is true in the config."""
    settings = load_analysis_settings(load_config_or_pass(config))
    return settings.classification.enabled


async def classification_build(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    seed_file: str | None = None,
) -> dict[str, object]:
    """Fit the classifier against the seed set; write the predictions.

    Returns ``{status, prediction_count, class_count, review_count,
    params}`` (plus reason/remedy on the honest no-op paths). Wave 0:
    disabled or unavailable, never a guess.
    """
    del pg_pool, tenant_id, seed_file  # the engine (a later wave) consumes these
    if not _classification_on(config):
        return _disabled_build()
    return _unavailable_build()


async def classification_list(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """The tenant's built predictions, deterministic order (bounded window).

    Wave 0: the read seam is not built; the envelope is the same honest
    no-op pair as the build op.
    """
    del pg_pool, tenant_id, limit  # the read seam (a later wave) consumes these
    if not _classification_on(config):
        return _disabled_list()
    return _unavailable_list()
