"""Shared catch-up plumbing for the legacy per-tenant projections.

DocumentsProjection and EmbedChunksProjection run the identical
drain-then-dispatch loop over the global notification_id sequence; this
module is that loop, once.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from corpus_kb.projections.event_reader import (
    DomainNotification,
    event_timestamp_dt,
)
from corpus_kb.projections.ids import deterministic_event_id


async def drain_sequence(
    reader: Any,
    tenant_id: UUID,
    checkpoint: Any,
    projection_name: str,
    handle,
) -> int:
    """Read since the checkpoint and handle every notification, until quiet."""
    total = 0
    for _ in range(20):
        cp = await checkpoint.get_checkpoint(projection_name, tenant_id)
        last_sequence = int(cp["last_sequence"]) if cp and cp["last_sequence"] else 0
        notifications = await reader.read_since(last_sequence, limit=500)
        if not notifications:
            break
        for notification in notifications:
            await handle(notification)
            total += 1
    return total


async def dispatch_notification(
    notification: DomainNotification,
    tenant_id: UUID,
    checkpoint: Any,
    projection_name: str,
    process_event,
) -> None:
    """Advance the shared sequence for foreign-tenant events; project ours."""
    timestamp = event_timestamp_dt(notification.event)
    event_id = deterministic_event_id(notification.originator_id, notification.originator_version)
    event_tenant = getattr(notification.event, "tenant_id", None)
    if event_tenant is not None and UUID(str(event_tenant)) != tenant_id:
        await checkpoint.update_checkpoint(
            projection_name,
            tenant_id,
            event_id,
            timestamp,
            notification.notification_id,
        )
        return
    payload = {
        key: value for key, value in vars(notification.event).items() if not key.startswith("_")
    }
    payload["aggregate_id"] = notification.originator_id
    await process_event(
        tenant_id,
        event_id,
        notification.event_type.split(".")[-1],
        payload,
        timestamp,
        last_sequence=notification.notification_id,
    )
