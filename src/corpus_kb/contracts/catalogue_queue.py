"""The operation catalogue: the ingest-queue family (textbook program W1).

Three ops over the durable work-order table: ``queue_enqueue``
(idempotent by content hash), ``queue_status`` (one row or the active
list), ``queue_cancel`` (pending/running only). Registry-first rule:
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

ENQUEUE_ENVELOPE = ("status", "queue_id", "content_hash", "deduplicated", "state")
STATUS_ENVELOPE = ("status", "entries", "count")
CANCEL_ENVELOPE = ("status", "queue_id", "state")

QUEUE_OPERATIONS: tuple[Operation, ...] = (
    Operation(
        name="queue_enqueue",
        family="queue",
        description=(
            "Queue one file for ingestion. Idempotent per content hash; a "
            "repeat answers the existing row with deduplicated."
        ),
        handler="corpus_kb.tools.queue_tools:queue_enqueue",
        params=(
            _TENANT,
            ParamSpec("path"),
            ParamSpec("title"),
            ParamSpec("profile"),
            ParamSpec("strategy_config"),
            ParamSpec("project_id"),
        ),
        envelope_keys=ENQUEUE_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "queue_enqueue"),
            SurfaceSpec(CLI, "queue-enqueue"),
            SurfaceSpec(HTTP, "/api/queue/enqueue", "POST"),
            SurfaceSpec(SOCKET, "queue_enqueue"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="queue_status",
        family="queue",
        description=(
            "One queue row by id, or the pending and running rows, oldest "
            "queued first. Progress keys stay null until a worker runs."
        ),
        handler="corpus_kb.tools.queue_tools:queue_status",
        params=(_TENANT, ParamSpec("queue_id"), ParamSpec("limit")),
        envelope_keys=STATUS_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "queue_status"),
            SurfaceSpec(CLI, "queue-status"),
            SurfaceSpec(HTTP, "/api/queue/status", "POST"),
            SurfaceSpec(SOCKET, "queue_status"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
    Operation(
        name="queue_cancel",
        family="queue",
        description=(
            "Cancel one pending or running queue row. A done, failed, or "
            "cancelled row answers a conflict error instead."
        ),
        handler="corpus_kb.tools.queue_tools:queue_cancel",
        params=(_TENANT, ParamSpec("queue_id")),
        envelope_keys=CANCEL_ENVELOPE,
        surfaces=(
            SurfaceSpec(MCP, "queue_cancel"),
            SurfaceSpec(CLI, "queue-cancel"),
            SurfaceSpec(HTTP, "/api/queue/cancel", "POST"),
            SurfaceSpec(SOCKET, "queue_cancel"),
        ),
        exit_codes=(0, 1, 2, 3),
    ),
)
