"""MCP/HTTP/socket/CLI handlers for the analysis ops (analysis program).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern:

  * :func:`analysis_backfill_dates` — the one-shot, idempotent
    ``documents.document_date`` backfill (migration 027's rule, callable
    after new ingests; no-op on already-dated documents).
  * :func:`analyze_run` — the composed analysis run: date coverage +
    labeling + classification + trends into one deterministic,
    snapshot-able report (WP-5).

The backfill engine is live (WP-1); the composed run is still the wave-0
stub. Every handler answers the house envelopes on every state — the
dormant default (every ``analysis.*.enabled`` false) is a no-op envelope
naming the flags; a missing migration answers ``unavailable``, never a
crash or fake data.

Envelope: ``{status, documents_scanned, documents_dated}`` for the
backfill, ``{status, sections}`` for the composed run — guaranteed on
every outcome, so the parity gate holds on the disabled path too.
"""

from __future__ import annotations

import asyncpg

from ..analysis.dates import (
    BACKFILL_RULE_VERSION,
    document_dates_present,
    run_document_date_backfill,
)
from ..analysis.settings import load_analysis_settings
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["analysis_backfill_dates", "analyze_run"]

_DISABLED_REASON = (
    "the analysis features are off in the loaded config "
    "(analysis.labeling.enabled, analysis.classification.enabled, "
    "analysis.topics.over_time.enabled are all false)."
)
_DISABLED_REMEDY = "Set the analysis.*.enabled flags you need to true, then retry."
_UNAVAILABLE_REASON = (
    "the composed analysis run is not built yet (the analysis program ships the registry first)"
)
_UNAVAILABLE_REMEDY = "keep the analysis.*.enabled flags false until the engines land"

_BACKFILL_DISABLED_REASON = "the date backfill needs analysis.topics.over_time.enabled: true."
_BACKFILL_DISABLED_REMEDY = "Set analysis.topics.over_time.enabled: true, then retry."
_BACKFILL_UNAVAILABLE_REASON = (
    "the documents.document_date column is missing (migration 027 has not run)."
)
_BACKFILL_UNAVAILABLE_REMEDY = "Run corpus-kb setup (or migration 027), then retry."


def _disabled_backfill() -> dict[str, object]:
    """The honest no-op envelope (backfill keys) for the dormant default."""
    return {
        "status": "disabled",
        "reason": _BACKFILL_DISABLED_REASON,
        "remedy": _BACKFILL_DISABLED_REMEDY,
        "documents_scanned": 0,
        "documents_dated": 0,
    }


def _unavailable_backfill() -> dict[str, object]:
    """The honest no-migration envelope (backfill keys): flag on, column absent."""
    return {
        "status": "unavailable",
        "reason": _BACKFILL_UNAVAILABLE_REASON,
        "remedy": _BACKFILL_UNAVAILABLE_REMEDY,
        "documents_scanned": 0,
        "documents_dated": 0,
    }


def _disabled_run() -> dict[str, object]:
    """The honest no-op envelope (run keys) for the dormant default."""
    return {
        "status": "disabled",
        "reason": _DISABLED_REASON,
        "remedy": _DISABLED_REMEDY,
        "sections": [],
    }


def _unavailable_run() -> dict[str, object]:
    """The honest wave-0 envelope (run keys): flags on, engines absent."""
    return {
        "status": "unavailable",
        "reason": _UNAVAILABLE_REASON,
        "remedy": _UNAVAILABLE_REMEDY,
        "sections": [],
    }


async def analysis_backfill_dates(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Backfill ``documents.document_date`` from chunk metadata.

    One tenant-scoped, idempotent UPDATE (the migration 027 rule: per
    document the earliest of published_time, modified_time, then the
    chunk's source_timestamp). Returns ``{status, documents_scanned,
    documents_dated}`` plus the rule version in ``params``; a second run
    dates nothing new.
    """
    settings = load_analysis_settings(load_config_or_pass(config))
    if not settings.over_time.enabled:
        return _disabled_backfill()
    if not await document_dates_present(pg_pool):
        return _unavailable_backfill()
    outcome = await run_document_date_backfill(pg_pool, tenant_id)
    return {
        "status": "success",
        "documents_scanned": outcome.documents_scanned,
        "documents_dated": outcome.documents_dated,
        "params": {"rule_version": BACKFILL_RULE_VERSION},
    }


async def analyze_run(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    output: str | None = None,
) -> dict[str, object]:
    """Run the composed corpus analysis; write the report artifact.

    Returns ``{status, sections}`` (plus reason/remedy on the honest
    no-op paths). Wave 0: disabled or unavailable, never a fake report.
    """
    del pg_pool, tenant_id, output  # the composed run (WP-5) consumes these
    settings = load_analysis_settings(load_config_or_pass(config))
    enabled = (
        settings.labeling.enabled,
        settings.classification.enabled,
        settings.over_time.enabled,
    )
    if not any(enabled):
        return _disabled_run()
    return _unavailable_run()
