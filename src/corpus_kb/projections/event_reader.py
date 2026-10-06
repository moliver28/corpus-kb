"""Event-store read path for projections (todo-11 STEP 0 spike, item (a)).

The eventsourcing library (>=9.0, PERSISTENCE_MODULE=eventsourcing.postgres)
owns the event store. Its ACTUAL table — verified against eventsourcing 9.5.5
on PostgreSQL 16 / pgvector 0.8.3 — is named after the application class
(``CorpusApplication`` -> ``public.corpusapplication_events``) and shaped:

    originator_id      UUID     NOT NULL,
    originator_version BIGINT   NOT NULL,
    topic              TEXT,     -- 'corpus_kb.domain.aggregates:Document.Ingested'
    state              BYTEA,    -- compressed JSON, decoded via the app Mapper
    notification_id    BIGSERIAL,
    PRIMARY KEY (originator_id, originator_version)
    + UNIQUE INDEX on notification_id

(this is the 9.x ApplicationRecorder shape; the plan's "stored_events" name
holds only for the DEFAULT application name — the mechanism is identical).

The pre-spike ``CheckpointManager.get_events_since`` polled
``event_store(event_id, aggregate_id, event_type, payload, created_at)`` — a
table that does not exist. This module replaces that read path: it polls by
``notification_id`` (ONE monotonic bigserial, so same-timestamp and
out-of-order events cannot be skipped — ``created_at`` is never consulted)
and decodes ``topic``/``state`` through the application's own Mapper, never a
hand-rolled transcoder. The event-store table is lib-owned and read-only to
this code path (SELECT only).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
from eventsourcing.persistence import StoredEvent

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def event_timestamp_dt(event: Any) -> datetime:
    """Aware datetime from a decoded event (lib yields int epoch or datetime)."""
    ts = event.timestamp
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return datetime.fromtimestamp(int(ts), tz=UTC)


def event_timestamp_iso(event: Any) -> str:
    """ISO timestamp from a decoded event (lib yields int epoch or datetime)."""
    return event_timestamp_dt(event).isoformat()


@dataclass(frozen=True)
class DomainNotification:
    """One decoded event from the lib's application events table.

    ``event`` is the decoded DomainEvent instance; its decorated-method
    params (including the mandatory ``tenant_id``) are plain attributes.
    """

    notification_id: int
    originator_id: str
    originator_version: int
    topic: str
    event: Any

    @property
    def event_type(self) -> str:
        """Topic suffix after the ``module:`` prefix, e.g. ``Document.Ingested``."""
        return self.topic.split(":", 1)[-1]


class EventReader:
    """Reads the lib's events table by notification_id, decoding via the Mapper."""

    def __init__(self, pool: asyncpg.Pool, mapper: Any, events_table: str) -> None:
        if not _IDENTIFIER.match(events_table):
            raise ValueError(f"unsafe events table name: {events_table!r}")
        self._pool = pool
        self._mapper = mapper
        self._events_table = events_table

    async def max_notification_id(self) -> int:
        """Current high-water mark of the global event sequence."""
        row = await self._pool.fetchrow(
            f"SELECT COALESCE(MAX(notification_id), 0) AS n FROM public.{self._events_table}"
        )
        return int(row["n"]) if row else 0

    async def read_since(self, last_sequence: int, limit: int = 500) -> list[DomainNotification]:
        """Return events with notification_id > last_sequence, in sequence order."""
        rows = await self._pool.fetch(
            f"""
            SELECT notification_id, originator_id, originator_version, topic, state
            FROM public.{self._events_table}
            WHERE notification_id > $1
            ORDER BY notification_id
            LIMIT $2
            """,
            last_sequence,
            limit,
        )
        notifications: list[DomainNotification] = []
        for row in rows:
            stored = StoredEvent(
                originator_id=row["originator_id"],
                originator_version=row["originator_version"],
                topic=row["topic"],
                state=bytes(row["state"]),
            )
            notifications.append(
                DomainNotification(
                    notification_id=int(row["notification_id"]),
                    originator_id=str(row["originator_id"]),
                    originator_version=int(row["originator_version"]),
                    topic=row["topic"],
                    event=self._mapper.to_domain_event(stored),
                )
            )
        return notifications
