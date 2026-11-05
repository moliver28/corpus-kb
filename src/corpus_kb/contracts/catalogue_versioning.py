"""The operation catalogue: corpus stats (Phase-2 Wave 3).

Two diagnostics reads over the corpus tables: ``stats`` (one row count
per table, event store included) and ``document_stats`` (the named
totals plus one row per source type). Both were handler-only before;
their GET routes existed without a catalogue row. Registry-first rule:
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

STATS_ENVELOPE = ("status", "stats")
DOCUMENT_STATS_ENVELOPE = ("status", "totals", "by_source_type")

VERSIONING_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="stats",
        family="versioning",
        description=(
            "Count the corpus rows: documents, chunks, vectors, entities, "
            "relations, tags, and event-store events."
        ),
        handler="corpus_kb.tools.versioning_tools:stats",
        params=(_TENANT,),
        envelope_keys=STATS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "stats"),
            SurfaceSpec(CLI, "stats"),
            SurfaceSpec(HTTP, "/api/stats", "GET"),
            SurfaceSpec(SOCKET, "stats"),
        ),
        exit_codes=(0, 1, 3),
    ),
    Operation(
        name="document_stats",
        family="versioning",
        description=(
            "Aggregate the document statistics: total rows per table plus "
            "one count per source type."
        ),
        handler="corpus_kb.tools.versioning_tools:document_stats",
        params=(_TENANT,),
        envelope_keys=DOCUMENT_STATS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "document_stats"),
            SurfaceSpec(CLI, "document-stats"),
            SurfaceSpec(HTTP, "/api/document-stats", "GET"),
            SurfaceSpec(SOCKET, "document_stats"),
        ),
        exit_codes=(0, 1, 3),
    ),
)
