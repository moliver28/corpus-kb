"""MCP/HTTP/socket/CLI handlers for the analysis ops (analysis program).

One handler per operation, shared by every surface — the
``tools/topics_tools.py`` pattern:

  * :func:`analysis_backfill_dates` — the one-shot, idempotent
    ``documents.document_date`` backfill (migration 027's rule,
    callable after new ingests; no-op on already-dated documents).
  * :func:`analyze_run` — the composed analysis run (WP-5): four
    sections in fixed order (dates-coverage, labeling, classification,
    trends), each DELEGATING to its one handler in
    ``tools/{labeling,classification,topics}_tools.py``. The section
    rows, the composer and the artifact writer live in
    ``analysis/report.py``. One failing section never fails the run.

Envelope: ``{status, documents_scanned, documents_dated}`` for the
backfill, ``{status, sections}`` for the composed run — guaranteed on
every outcome, so the parity gate holds on the disabled path too. The
dormant default keeps the wave-0 stub envelope exactly; success gains
``report_path`` when an output path was requested.
"""

from __future__ import annotations

import asyncpg

from ..analysis import report as report_mod
from ..analysis.dates import (
    BACKFILL_RULE_VERSION,
    document_dates_present,
    run_document_date_backfill,
)
from ..analysis.settings import AnalysisSettings, load_analysis_settings
from ..classification import store as classification_store
from . import classification_tools, labeling_tools, topics_tools
from .ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["analysis_backfill_dates", "analyze_run"]

_DISABLED_REASON = (
    "the analysis features are off in the loaded config "
    "(analysis.labeling.enabled, analysis.classification.enabled, "
    "analysis.topics.over_time.enabled are all false)."
)
_DISABLED_REMEDY = "Set the analysis.*.enabled flags you need to true, then retry."
_BACKFILL_DISABLED_REASON = "the date backfill needs analysis.topics.over_time.enabled: true."
_BACKFILL_DISABLED_REMEDY = "Set analysis.topics.over_time.enabled: true, then retry."
_BACKFILL_UNAVAILABLE_REASON = (
    "the documents.document_date column is missing (migration 027 has not run)."
)
_BACKFILL_UNAVAILABLE_REMEDY = "Run corpus-kb setup (or migration 027), then retry."

# The flags-on/no-data corner: sections answered honestly, none succeeded.
_RUN_NO_DATA_REASON = "the analysis flags are on but no section produced data."
_RUN_NO_DATA_REMEDY = "Read the section statuses. Fix the named gaps. Then retry."

# analyze never invents seeds: no stored set, no classification build.
_SKIPPED_REASON = "no seed label set; run classification-build with a seed file first"
_SKIPPED_REMEDY = "Write a seed JSON file, then run classification-build with it."


def _honest_backfill(status: str, reason: str, remedy: str) -> dict[str, object]:
    """An honest no-op envelope on the backfill keys (reason + remedy named)."""
    return {
        "status": status,
        "reason": reason,
        "remedy": remedy,
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
        return _honest_backfill("disabled", _BACKFILL_DISABLED_REASON, _BACKFILL_DISABLED_REMEDY)
    if not await document_dates_present(pg_pool):
        return _honest_backfill(
            "unavailable", _BACKFILL_UNAVAILABLE_REASON, _BACKFILL_UNAVAILABLE_REMEDY
        )
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

    Four sections in fixed order; each delegates to its ONE handler and
    answers on its own. With ``output`` set, the report lands as JSON
    plus a rendered markdown beside it (see
    ``analysis/report.write_artifact``), and the success envelope gains
    ``report_path``. Nothing is written while dormant.
    """
    settings = load_analysis_settings(load_config_or_pass(config))
    if not any(
        (settings.labeling.enabled, settings.classification.enabled, settings.over_time.enabled)
    ):
        return _disabled_run()
    sections = [
        await _dates_section(pg_pool, config, tenant_id, settings),
        await _labeling_section(pg_pool, config, tenant_id, settings),
        await _classification_section(pg_pool, config, tenant_id, settings),
        await _trends_section(pg_pool, config, tenant_id, settings),
    ]
    composed = report_mod.compose_report(sections)
    status = str(composed["status"])
    envelope = dict(composed)
    if status != "success":
        envelope["reason"] = _RUN_NO_DATA_REASON
        envelope["remedy"] = _RUN_NO_DATA_REMEDY
    if output is not None:
        path = report_mod.write_artifact(status, sections, output)
        if status == "success":
            envelope["report_path"] = path
    return envelope


async def _dates_section(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None,
    tenant_id: str,
    settings: AnalysisSettings,
) -> report_mod.AnalysisSection:
    """Date coverage: the backfill envelope IS the coverage report."""
    if not settings.over_time.enabled:
        return report_mod.disabled_section(
            "dates-coverage",
            "analysis.topics.over_time.enabled is false; the date backfill needs it.",
            "Set analysis.topics.over_time.enabled: true, then retry.",
        )
    return await report_mod.run_section(
        "dates-coverage", lambda: analysis_backfill_dates(pg_pool, config, tenant_id)
    )


async def _labeling_section(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None,
    tenant_id: str,
    settings: AnalysisSettings,
) -> report_mod.AnalysisSection:
    """Weak-supervision labels: needs the flag AND an LF file."""
    if not settings.labeling.enabled:
        return report_mod.disabled_section(
            "labeling",
            "analysis.labeling.enabled is false in the loaded config.",
            "Set analysis.labeling.enabled: true, then retry.",
        )
    if not settings.labeling.lfs_file:
        return report_mod.disabled_section(
            "labeling",
            "analysis.labeling.lfs_file is not set; the build has no functions to score.",
            "Point analysis.labeling.lfs_file at the labeling-functions JSON file.",
        )
    return await report_mod.run_section(
        "labeling", lambda: labeling_tools.labeling_build(pg_pool, config, tenant_id)
    )


async def _classification_section(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None,
    tenant_id: str,
    settings: AnalysisSettings,
) -> report_mod.AnalysisSection:
    """Few-shot predictions: analyze never invents seeds.

    No stored set -> ``skipped``. With one, the build handler answers
    (its honest guard names the seed file when the composed run has
    none to pass — the stored set keeps the file name only, so a re-fit
    stays with ``classification-build``).
    """
    if not settings.classification.enabled:
        return report_mod.disabled_section(
            "classification",
            "analysis.classification.enabled is false in the loaded config.",
            "Set analysis.classification.enabled: true, then retry.",
        )

    async def run() -> dict[str, object]:
        if not await classification_store.classification_tables_present(pg_pool):
            # The handler owns the honest unavailable envelope (no 029).
            return await classification_tools.classification_build(pg_pool, config, tenant_id)
        seeded = await classification_store.read_predictions(pg_pool, tenant_id, 1)
        if not seeded:
            return {"status": "skipped", "reason": _SKIPPED_REASON, "remedy": _SKIPPED_REMEDY}
        return await classification_tools.classification_build(pg_pool, config, tenant_id)

    return await report_mod.run_section("classification", run)


async def _trends_section(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None,
    tenant_id: str,
    settings: AnalysisSettings,
) -> report_mod.AnalysisSection:
    """Topic trends: refresh dates first, then the trends handler."""
    if not settings.over_time.enabled:
        return report_mod.disabled_section(
            "trends",
            "analysis.topics.over_time.enabled is false in the loaded config.",
            "Set analysis.topics.over_time.enabled: true, then retry.",
        )

    async def run() -> dict[str, object]:
        refresh = await analysis_backfill_dates(pg_pool, config, tenant_id)
        envelope = await topics_tools.topics_trends(pg_pool, config, tenant_id)
        detail = {key: value for key, value in envelope.items() if key != "status"}
        detail["date_backfill"] = {
            "status": str(refresh.get("status", "unknown")),
            "documents_dated": refresh.get("documents_dated", 0),
        }
        return {"status": str(envelope.get("status", "failed")), **detail}

    return await report_mod.run_section("trends", run)
