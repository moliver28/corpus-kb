"""MCP/HTTP/socket/CLI handlers for the labeling ops (analysis program W0).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern:

  * :func:`labeling_build` — the offline weak-supervision run: score the
    tenant's labeling functions over the corpus, aggregate into
    probabilistic chunk labels (wholesale write).
  * :func:`labeling_list` — the tenant's built chunk labels.
  * :func:`labeling_lfs` — the tenant's registered labeling functions.

Wave 0 is the registry-first skeleton: the catalogue rows and the honest
envelopes exist, the engines land later. Every handler answers the house
envelopes on every state — the dormant default
(``analysis.labeling.enabled: false``) is a no-op envelope naming the
flag; an enabled flag on the skeleton is ``unavailable`` (the engine is
not built yet), never a crash or fake data. When the engine lands, the
list ops drop the flag gate (reads are never gated — the
``topics_list`` rule) and keep the pre-migration ``unavailable`` check.

Envelope: ``{status, label_count, labels}`` for the build/list pair,
``{status, lf_count, lfs}`` for the LF registry read — guaranteed on
every outcome, so the parity gate holds on the disabled path too.
"""

from __future__ import annotations

import asyncpg

from ..analysis.settings import load_analysis_settings
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["labeling_build", "labeling_lfs", "labeling_list"]

# Read/list bounds (the ingest_status convention: bounded, honest windows).
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 500

_DISABLED_REASON = "analysis.labeling.enabled is false in the loaded config."
_DISABLED_REMEDY = "Set analysis.labeling.enabled: true, then retry."
_UNAVAILABLE_REASON = (
    "the labeling engine is not built yet (the analysis program ships the registry first)"
)
_UNAVAILABLE_REMEDY = "keep analysis.labeling.enabled: false until the labeling engine lands"


def _disabled_labels() -> dict[str, object]:
    """The honest no-op envelope (label keys) for the dormant default."""
    return {
        "status": "disabled",
        "reason": _DISABLED_REASON,
        "remedy": _DISABLED_REMEDY,
        "label_count": 0,
        "labels": [],
    }


def _unavailable_labels() -> dict[str, object]:
    """The honest wave-0 envelope (label keys): flag on, engine absent."""
    return {
        "status": "unavailable",
        "reason": _UNAVAILABLE_REASON,
        "remedy": _UNAVAILABLE_REMEDY,
        "label_count": 0,
        "labels": [],
    }


def _disabled_lfs() -> dict[str, object]:
    """The honest no-op envelope (LF keys) for the dormant default."""
    return {
        "status": "disabled",
        "reason": _DISABLED_REASON,
        "remedy": _DISABLED_REMEDY,
        "lf_count": 0,
        "lfs": [],
    }


def _unavailable_lfs() -> dict[str, object]:
    """The honest wave-0 envelope (LF keys): flag on, engine absent."""
    return {
        "status": "unavailable",
        "reason": _UNAVAILABLE_REASON,
        "remedy": _UNAVAILABLE_REMEDY,
        "lf_count": 0,
        "lfs": [],
    }


def _labeling_on(config: dict[str, object] | None) -> bool:
    """True when ``analysis.labeling.enabled`` is true in the config."""
    settings = load_analysis_settings(load_config_or_pass(config))
    return settings.labeling.enabled


async def labeling_build(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Score the labeling functions and write the aggregated labels.

    Returns ``{status, label_count, labels}`` (plus reason/remedy on the
    honest no-op paths). Wave 0: disabled or unavailable, never a guess.
    """
    del pg_pool, tenant_id  # the engine (a later wave) consumes the pool + tenant
    if not _labeling_on(config):
        return _disabled_labels()
    return _unavailable_labels()


async def labeling_list(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """The tenant's built chunk labels, deterministic order (bounded window).

    Wave 0: the read seam is not built; the envelope is the same honest
    no-op pair as the build op.
    """
    del pg_pool, tenant_id, limit  # the read seam (a later wave) consumes these
    if not _labeling_on(config):
        return _disabled_labels()
    return _unavailable_labels()


async def labeling_lfs(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, object]:
    """The tenant's registered labeling functions, deterministic order.

    Wave 0: the registry seam is not built; the envelope is the same
    honest no-op pair as the build op.
    """
    del pg_pool, tenant_id, limit  # the registry seam (a later wave) consumes these
    if not _labeling_on(config):
        return _disabled_lfs()
    return _unavailable_lfs()
