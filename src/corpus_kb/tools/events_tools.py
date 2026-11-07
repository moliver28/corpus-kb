"""The event-store health op: ONE shared function (issue #350).

``events_summary`` is the read every surface serves — the generated MCP
tool, the socket spec arm, the CLI ``corpus-kb events`` command, and the
GET route all route through here. The body lives in
``handlers.events_ops``; this module adds tenant validation only.
"""

from __future__ import annotations

from typing import Any

from corpus_kb.tools._uuid_input import uuid_arg
from corpus_kb.tools.ingest_common import DEFAULT_TENANT_ID, load_config_or_pass

__all__ = ["events_summary"]


async def events_summary(
    pg_pool: Any,
    config: dict[str, Any] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Event counts by topic, latest notification id, checkpoints, DLQ, runs.

    The event store resolves inside the handler (Postgres table, or the
    SQLite sidecar on the duckdb backend); callers never touch store
    files. Envelope: ``{status, events_store, events_by_topic,
    latest_notification_id, checkpoints, dead_letters, recent_runs}``.
    """
    cfg = load_config_or_pass(config)
    uuid_arg(tenant_id, "tenant_id")
    from corpus_kb.handlers.events_ops import events_summary as summary_body

    return await summary_body(pg_pool, tenant_id, config=cfg)
