"""Shared catch-up plumbing for the legacy per-tenant projections.

DocumentsProjection and EmbedChunksProjection run the identical
drain-then-dispatch loop over the global notification_id sequence; this
module is that loop, once.

Legacy events (issue #143): events persisted before the ChunksAdded
tenant stamp carry no ``tenant_id`` payload. The store is append-only,
so history is never rewritten — the drain resolves the owning tenant
from the aggregate's creation event instead, and only an unresolvable
event falls back to the declared default tenant.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from corpus_kb.domain.models import DEFAULT_TENANT_ID
from corpus_kb.projections.event_reader import (
    DomainNotification,
    event_timestamp_dt,
)
from corpus_kb.projections.ids import deterministic_event_id

logger = logging.getLogger(__name__)

#: The drain-side owner lookup: one originator id in, its owning tenant
#: out — or None when the owner cannot be resolved.
OwnerResolver = Callable[[str], Awaitable[UUID | None]]


def legacy_owner_resolver(reader: Any) -> OwnerResolver:
    """The owner lookup for one reader; readers without one resolve None."""

    async def resolve(originator_id: str) -> UUID | None:
        lookup = getattr(reader, "owner_tenant", None)
        if lookup is None:
            return None
        try:
            return await lookup(originator_id)
        except Exception as exc:
            logger.warning("Owner lookup failed for aggregate %s: %s", originator_id, exc)
            return None

    return resolve


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
    resolve_owner: OwnerResolver | None = None,
) -> None:
    """Advance the shared sequence for foreign-tenant events; project ours.

    A tenant-less event (the legacy population) resolves its owner through
    the aggregate's creation event; an unresolvable one counts as the
    declared default tenant, so foreign text never replays here.
    """
    timestamp = event_timestamp_dt(notification.event)
    event_id = deterministic_event_id(notification.originator_id, notification.originator_version)
    event_tenant = getattr(notification.event, "tenant_id", None)
    if event_tenant is None and resolve_owner is not None:
        event_tenant = await resolve_owner(notification.originator_id)
    if event_tenant is None:
        event_tenant = DEFAULT_TENANT_ID
    if UUID(str(event_tenant)) != tenant_id:
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
