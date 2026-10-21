"""EventReader._decode: the corrupt-store guard (a None state fails typed).

A Notification.state of None is not a decodable event — the lib's rows are
compressed JSON and None means store damage. The reader must fail with a
typed error instead of a raw ``TypeError`` from ``bytes(None)``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from corpus_kb.errors import InternalError
from corpus_kb.projections.event_reader import EventReader

ROW = (7, str(UUID(int=1)), 1, "corpus_kb.domain.aggregates:Document.Ingested")


class _StubMapper:
    def to_domain_event(self, stored_event: Any) -> Any:
        return stored_event


def _reader() -> EventReader:
    # The recorder slot only unlocks the no-pool construction; _decode never
    # touches it.
    return EventReader(None, _StubMapper(), "corpusapplication_events", recorder=object())


def test_none_state_fails_typed_as_corrupt_store() -> None:
    with pytest.raises(InternalError, match="no state"):
        _reader()._decode(*ROW, None)


def test_bytes_state_decodes_into_a_notification() -> None:
    note = _reader()._decode(*ROW, b"{}")
    assert note.notification_id == 7
    assert note.event_type == "Document.Ingested"
