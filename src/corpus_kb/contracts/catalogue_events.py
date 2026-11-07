"""The operation catalogue: the event-store health family (issue #350).

``events_summary`` is the ONE op that replaces what plugin 0.6.0 did with
four SQL queries plus a direct read of the DuckDB event-store sidecar:
event counts by topic, the latest notification id, each projection
checkpoint, the dead-letter count with the newest five entries, and the
last five ingest runs. The event-store knowledge (the SQLite sidecar
path on the duckdb backend) stays in the engine
(``handlers.events_ops``); callers never touch store files.

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

EVENTS_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="events_summary",
        family="events",
        description=(
            "Event-store health: counts by topic, latest notification id, "
            "checkpoints, dead letters, recent ingest runs."
        ),
        handler="corpus_kb.tools.events_tools:events_summary",
        params=(ParamSpec("tenant_id", default=DEFAULT_TENANT_ID),),
        envelope_keys=(
            "status",
            "events_store",
            "events_by_topic",
            "latest_notification_id",
            "checkpoints",
            "dead_letters",
            "recent_runs",
        ),
        surfaces=(
            SurfaceSpec(MCP, "events_summary"),
            SurfaceSpec(CLI, "events"),
            SurfaceSpec(HTTP, "/api/events/summary", "GET"),
            SurfaceSpec(SOCKET, "events_summary"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
