"""Drain-side tenant resolution for legacy events (issue #143).

Unit pins for ``projections/_drain.dispatch_notification``:

* a tenant-less (pre-stamp) event resolves its owner through the
  aggregate's creation event and skips like any foreign event;
* an unresolvable owner counts as the declared default tenant, so a
  foreign drain never replays it;
* the default-tenant drain keeps the old behavior: the event replays.

Pure stubs: no DuckDB, no Postgres, no event store.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from corpus_kb.domain.models import DEFAULT_TENANT_ID
from corpus_kb.projections._drain import dispatch_notification

TENANT = UUID("00000000-0000-0000-0000-000000000001")
OTHER = UUID("00000000-0000-0000-0000-000000000002")


@dataclass(frozen=True)
class _LegacyEvent:
    """The tenant-less event shape: no ``tenant_id`` attribute at all."""

    timestamp: datetime = datetime(1, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class _Notification:
    """The one notification shape the drain reads."""

    notification_id: int
    originator_id: str
    originator_version: int
    topic: str
    event: Any = None

    @property
    def event_type(self) -> str:
        return self.topic.split(":", 1)[-1]


class _Checkpoint:
    """Records every advance so the test can tell skip from replay."""

    def __init__(self) -> None:
        self.advanced: list[int] = []

    async def update_checkpoint(
        self, _name: str, _tenant: UUID, _event_id: UUID, _ts: object, sequence: int
    ) -> None:
        self.advanced.append(sequence)


async def _returns_other(_originator_id: str) -> UUID:
    return OTHER


async def _returns_none(_originator_id: str) -> None:
    return None


async def _run(
    drainer: UUID, resolve_owner: Callable[[str], Awaitable[UUID | None]] | None
) -> tuple[_Checkpoint, list[str]]:
    checkpoint = _Checkpoint()
    projected: list[str] = []

    async def process_event(tenant_id: UUID, *_args: Any, **_kw: Any) -> None:
        projected.append(str(tenant_id))

    note = _Notification(
        9,
        str(uuid4()),
        2,
        "corpus_kb.domain.aggregates:Document.ChunksAdded",
        event=_LegacyEvent(),
    )
    await dispatch_notification(
        note,
        drainer,
        checkpoint,
        "DocumentsProjection",
        process_event,
        resolve_owner=resolve_owner,
    )
    return checkpoint, projected


async def test_legacy_event_resolves_foreign_owner_and_skips() -> None:
    checkpoint, projected = await _run(TENANT, _returns_other)
    assert checkpoint.advanced == [9], "a resolved foreign legacy event advances in one pass"
    assert projected == [], "foreign text must never project"


async def test_unresolvable_legacy_event_counts_as_the_default_tenant() -> None:
    checkpoint, projected = await _run(OTHER, _returns_none)
    assert checkpoint.advanced == [9], "the default tenant is foreign to this drain: skip"
    assert projected == []


async def test_default_tenant_drain_replays_an_unresolvable_event() -> None:
    checkpoint, projected = await _run(DEFAULT_TENANT_ID, None)
    assert checkpoint.advanced == [], "nothing advanced: the event projected"
    assert projected == [str(DEFAULT_TENANT_ID)], "the old single-tenant behavior is kept"
