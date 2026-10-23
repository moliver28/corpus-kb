"""The operation catalogue: the conflicts family (Living Corpus W3).

Three ops over the statement-conflict flags: ``conflicts_scan`` (run
the cascade), ``conflicts_list`` (read flags), ``conflicts_resolve``
(human finalize; a confirmed revision supersedes the older claim).
Registry-first rule: change the row first, then the surfaces, then the
gates. All strings are plain English (ASD-STE100).
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

SCAN_ENVELOPE = ("status", "scanned", "flagged", "verdicts")
CONFLICTS_LIST_ENVELOPE = ("status", "conflicts", "count")
RESOLVE_ENVELOPE = ("status", "conflict_id", "resolved_verdict")

CONFLICTS_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="conflicts_scan",
        family="conflicts",
        description=(
            "Run the contradiction cascade over live statements: temporal "
            "gate, structured swaps, then the NLI judge. Records flags."
        ),
        handler="corpus_kb.tools.conflicts_tools:conflicts_scan",
        params=(_TENANT,),
        envelope_keys=SCAN_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "conflicts_scan"),
            SurfaceSpec(CLI, "conflicts-scan"),
            SurfaceSpec(HTTP, "/api/conflicts/scan", "POST"),
            SurfaceSpec(SOCKET, "conflicts_scan"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="conflicts_list",
        family="conflicts",
        description="List recorded conflict flags newest-first, filterable by verdict.",
        handler="corpus_kb.tools.conflicts_tools:conflicts_list",
        params=(_TENANT, ParamSpec("verdict"), ParamSpec("limit")),
        envelope_keys=CONFLICTS_LIST_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "conflicts_list"),
            SurfaceSpec(CLI, "conflicts-list"),
            SurfaceSpec(HTTP, "/api/conflicts/list", "POST"),
            SurfaceSpec(SOCKET, "conflicts_list"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="conflicts_resolve",
        family="conflicts",
        description=(
            "Finalize one conflict flag. A confirmed revision supersedes "
            "the older statement; the flag is never deleted."
        ),
        handler="corpus_kb.tools.conflicts_tools:conflicts_resolve",
        params=(
            _TENANT,
            ParamSpec("conflict_id"),
            ParamSpec("resolved_verdict"),
            ParamSpec("resolved_by"),
        ),
        envelope_keys=RESOLVE_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "conflicts_resolve"),
            SurfaceSpec(CLI, "conflicts-resolve"),
            SurfaceSpec(HTTP, "/api/conflicts/resolve", "POST"),
            SurfaceSpec(SOCKET, "conflicts_resolve"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
