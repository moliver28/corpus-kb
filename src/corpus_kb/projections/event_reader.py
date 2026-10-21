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

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg
from eventsourcing.persistence import StoredEvent

from corpus_kb.errors import InternalError

logger = logging.getLogger(__name__)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def event_timestamp_dt(event: Any) -> datetime:
    """Aware datetime from a decoded event (lib yields int epoch or datetime)."""
    ts = event.timestamp
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return datetime.fromtimestamp(int(ts), tz=UTC)


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
    """Reads the event store by notification_id, decoding via the Mapper.

    Two read modes behind one API (storage portability program §7):

    - ``recorder`` given: the eventsourcing recorder API
      (``select_notifications``) — the portability path. On a duckdb
      deployment the event store is the lib's SQLite file, which only the
      recorder may open; the read runs in a worker thread because recorder
      calls are synchronous. The same API serves the Postgres recorder.
    - no ``recorder`` (the pinned positional construction): the legacy
      Postgres SELECT over the lib's events table, byte-identical to the
      pre-port reader.
    """

    def __init__(
        self,
        pool: asyncpg.Pool | None,
        mapper: Any,
        events_table: str,
        *,
        recorder: Any = None,
    ) -> None:
        self._pool = pool
        self._mapper = mapper
        self._events_table = events_table
        self._recorder = recorder
        if recorder is None:
            if not _IDENTIFIER.match(events_table):
                raise ValueError(f"unsafe events table name: {events_table!r}")
            if pool is None:
                raise ValueError("EventReader needs a pool when no recorder is given")

    async def read_since(self, last_sequence: int, limit: int = 500) -> list[DomainNotification]:
        """Return events with notification_id > last_sequence, in sequence order."""
        if self._recorder is not None:
            return await self._read_since_recorder(last_sequence, limit)
        return await self._read_since_pool(last_sequence, limit)

    async def _read_since_recorder(
        self, last_sequence: int, limit: int
    ) -> list[DomainNotification]:
        # inclusive_of_start=False keeps the strict "greater than the stored
        # position" contract the pool path has always had.
        notifications = await asyncio.to_thread(
            lambda: self._recorder.select_notifications(
                start=last_sequence, limit=limit, inclusive_of_start=False
            )
        )
        return [
            self._decode(n.id, n.originator_id, n.originator_version, n.topic, n.state)
            for n in notifications
        ]

    async def _read_since_pool(self, last_sequence: int, limit: int) -> list[DomainNotification]:
        assert self._pool is not None
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
        return [
            self._decode(
                row["notification_id"],
                row["originator_id"],
                row["originator_version"],
                row["topic"],
                row["state"],
            )
            for row in rows
        ]

    async def owner_tenant(self, originator_id: str) -> UUID | None:
        """The tenant that owns this aggregate, read from its first event.

        Drain-side resolution for legacy events persisted before the
        ``ChunksAdded`` tenant stamp (issue #143). The event store is
        append-only, so history is never rewritten: the owner comes from
        the aggregate's creation event, which always carried ``tenant_id``.
        None means unresolvable; the drain then applies its declared
        default.
        """
        try:
            stored = await self._first_stored_event(originator_id)
            if stored is None:
                return None
            owner = getattr(self._mapper.to_domain_event(stored), "tenant_id", None)
        except Exception as exc:
            logger.warning("Owner lookup failed for aggregate %s: %s", originator_id, exc)
            return None
        if owner is None:
            return None
        try:
            return UUID(str(owner))
        except ValueError:
            logger.warning("Aggregate %s carries a bad tenant_id %r", originator_id, owner)
            return None

    async def _first_stored_event(self, originator_id: str) -> StoredEvent | None:
        """The aggregate's first stored event (the creation event).

        The id normalizes to ``UUID`` first: the SQLite recorder stores
        ``originator_id.hex``, so a dashed string never matches.
        """
        aggregate = originator_id if isinstance(originator_id, UUID) else UUID(str(originator_id))
        if self._recorder is not None:
            rows = await asyncio.to_thread(lambda: self._recorder.select_events(aggregate, limit=1))
            return rows[0] if rows else None
        assert self._pool is not None
        row = await self._pool.fetchrow(
            f"""
            SELECT originator_id, originator_version, topic, state
            FROM public.{self._events_table}
            WHERE originator_id = $1
            ORDER BY originator_version
            LIMIT 1
            """,
            aggregate,
        )
        if row is None:
            return None
        return StoredEvent(
            originator_id=row["originator_id"],
            originator_version=row["originator_version"],
            topic=row["topic"],
            state=bytes(row["state"]),
        )

    def _decode(
        self,
        notification_id: int,
        originator_id: Any,
        originator_version: int,
        topic: str,
        state: Any,
    ) -> DomainNotification:
        """One stored row -> DomainNotification via the app's Mapper.

        ``state`` is the lib's compressed JSON. A None state means a
        corrupt store row; that fails typed here rather than tripping a
        raw ``TypeError`` inside ``bytes(state)``.
        """
        if state is None:
            raise InternalError(
                f"Event store row {notification_id} carries no state.",
                remediation="Check the event store table for damage. Restore it, then retry.",
            )
        stored = StoredEvent(
            originator_id=originator_id,
            originator_version=originator_version,
            topic=topic,
            state=bytes(state),
        )
        return DomainNotification(
            notification_id=int(notification_id),
            originator_id=str(originator_id),
            originator_version=int(originator_version),
            topic=topic,
            event=self._mapper.to_domain_event(stored),
        )
