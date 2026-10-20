"""The operation catalogue: the analysis family (analysis program W0).

``analysis_backfill_dates`` is the one-shot date-provenance backfill
(WP-1: chunks metadata -> ``documents.document_date``, the migration 027
rule, callable after new ingests). ``analyze_run`` is the composed
corpus analysis (WP-5): date coverage + labeling + classification +
trends into one deterministic, snapshot-able report. Wave 0 ships the
registry rows and the honest envelopes; the engines land in later waves,
so every outcome on the skeleton is ``disabled`` (the dormant default)
or ``unavailable`` (flag on, engine not built). Registry-first rule:
change the row first, then the surfaces, then the gates. All strings are
plain English (ASD-STE100).
"""

from __future__ import annotations

from corpus_kb.contracts.model import (
    CLI,
    DEFAULT_TENANT_ID,
    HTTP,
    MCP,
    SOCKET,
    Operation,
    ParamSpec,
    SurfaceSpec,
)

_TENANT = ParamSpec("tenant_id", default=DEFAULT_TENANT_ID)

# Guaranteed-on-success keys only: the disabled/unavailable reasons stay
# off the tuple (the envelopes.py rule).
BACKFILL_ENVELOPE = ("status", "documents_scanned", "documents_dated")
ANALYZE_ENVELOPE = ("status", "sections")

ANALYSIS_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="analysis_backfill_dates",
        family="analysis",
        description=(
            "Backfill documents.document_date from chunk metadata (the "
            "migration 027 rule, idempotent). Gated behind "
            "analysis.topics.over_time.enabled."
        ),
        handler="corpus_kb.tools.analysis_tools:analysis_backfill_dates",
        params=(_TENANT,),
        envelope_keys=BACKFILL_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "analysis_backfill_dates"),
            SurfaceSpec(CLI, "analysis-backfill-dates"),
            SurfaceSpec(HTTP, "/api/analysis/backfill-dates", "POST"),
            SurfaceSpec(SOCKET, "analysis_backfill_dates"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="analyze_run",
        family="analysis",
        description=(
            "Run the composed corpus analysis (dates, labels, "
            "predictions, trends) and write the report. Honest no-op "
            "while every analysis flag is false."
        ),
        handler="corpus_kb.tools.analysis_tools:analyze_run",
        params=(ParamSpec("output", default=None), _TENANT),
        envelope_keys=ANALYZE_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "analyze_run"),
            SurfaceSpec(CLI, "analyze-run"),
            SurfaceSpec(HTTP, "/api/analyze", "POST"),
            SurfaceSpec(SOCKET, "analyze_run"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
