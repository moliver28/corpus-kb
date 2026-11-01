"""The operation catalogue: the statements family (Living Corpus W2).

Two read ops over the statement atoms: ``statements_list`` (newest-first
page, filterable by template and document) and ``statements_timeline``
(chronological history view, the belief-history leg). Registry-first
rule: change the row first, then the surfaces, then the gates. All
strings are plain English (ASD-STE100).
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

LIST_ENVELOPE = ("status", "statements", "count")
TIMELINE_ENVELOPE = ("status", "statements", "count")

STATEMENTS_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="statements_list",
        family="statements",
        description=(
            "List statement atoms newest-first. Filters: template_id, document_id, limit."
        ),
        handler="corpus_kb.tools.statements_tools:statements_list",
        params=(
            _TENANT,
            ParamSpec("template_id"),
            ParamSpec("document_id"),
            # Issue #238: the page size lives here, not in the adapters.
            ParamSpec("limit", default=50),
        ),
        envelope_keys=LIST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "statements_list"),
            SurfaceSpec(CLI, "statements-list"),
            SurfaceSpec(HTTP, "/api/statements/list", "POST"),
            SurfaceSpec(SOCKET, "statements_list"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="statements_timeline",
        family="statements",
        description=(
            "Chronological statement history for one text query — the "
            "belief-history view (valid_at first, supersede state shown)."
        ),
        handler="corpus_kb.tools.statements_tools:statements_timeline",
        params=(_TENANT, ParamSpec("text_query"), ParamSpec("limit", default=200)),
        envelope_keys=TIMELINE_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "statements_timeline"),
            SurfaceSpec(CLI, "statements-timeline"),
            SurfaceSpec(HTTP, "/api/statements/timeline", "POST"),
            SurfaceSpec(SOCKET, "statements_timeline"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
