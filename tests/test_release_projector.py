"""Release projector — replay determinism and guarded transitions.

Fake-conn discipline (Mimosa): a Protocol-typed recorder whose execute only
MEMORIZES (sql, args); every SQL string asserted here is a module constant
imported from the projector source, never inline test SQL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Protocol
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from corpus_kb.projections.research.release_projector import (
    SQL_INSERT_RELEASE,
    SQL_INSERT_WAIVER,
    SQL_RELEASE,
    SQL_SUPERSEDE,
    SQL_UPSERT_GATE,
    ReleaseProjector,
)

TENANT = UUID("00000000-0000-0000-0000-000000000001")
VERSION_ID = UUID("33333333-3333-3333-3333-333333333333")
RELEASE_ID = UUID("11111111-1111-1111-1111-111111111111")
PARENT_ID = UUID("44444444-4444-4444-4444-444444444444")
SUCCESSOR_ID = UUID("55555555-5555-5555-5555-555555555555")
MANIFEST = {"schema_version": "corpus-kb.release-manifest/1", "release_id": str(RELEASE_ID)}


class RecordingConn(Protocol):
    async def execute(self, sql: str, *args: object) -> str: ...


class FakeConn:
    """Protocol-typed recorder: no SQL executes, everything is memorized."""

    def __init__(self, results: Sequence[str] = ()) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._results = list(results)

    async def execute(self, sql: str, *args: object) -> str:
        self.calls.append((sql, args))
        return self._results.pop(0) if self._results else "UPDATE 1"


def _conn_patch(fake: FakeConn):
    @asynccontextmanager
    async def fake_tenant_connection(pool: object, tenant_id: UUID) -> AsyncIterator[RecordingConn]:
        assert str(tenant_id) == str(TENANT)
        yield fake

    return fake_tenant_connection


def _notification(name: str, **attrs: object) -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(**attrs),
        originator_id=VERSION_ID,
        topic=f"CodebookVersion.{name}",
        notification_id=1,
    )


def _lifecycle_notifications() -> list[SimpleNamespace]:
    """One full release lifecycle as the projector would receive it."""
    return [
        _notification(
            "CodebookReleaseRequested",
            tenant_id=str(TENANT),
            release_id=str(RELEASE_ID),
            codebook_id=str(PARENT_ID),
            profile="team-codebook",
            creator="alice",
            manifest_json=MANIFEST,
            manifest_sha256="a" * 64,
            requested_at="2026-10-09T00:00:00+00:00",
            parent_release_id=None,
        ),
        _notification(
            "GateEvaluated",
            tenant_id=str(TENANT),
            release_id=str(RELEASE_ID),
            gate_id="clustering_determinism",
            status="pass",
            value={"seed": 42},
            threshold={"max_variance": 0.0},
            reason="converged",
            evidence_refs=["sweep-1"],
            evaluator={"gate_fn": "gate_clustering_determinism"},
            evaluated_at="2026-10-09T00:01:00+00:00",
        ),
        _notification(
            "WaiverRecorded",
            tenant_id=str(TENANT),
            release_id=str(RELEASE_ID),
            gate_id="audit_sample_design",
            justification="audit lands next sprint",
            approver="dana",
            recorded_at="2026-10-09T00:01:30+00:00",
        ),
        _notification(
            "CodebookReleased",
            tenant_id=str(TENANT),
            release_id=str(RELEASE_ID),
            approver="dana",
            released_at="2026-10-09T00:02:00+00:00",
        ),
        _notification(
            "CodebookSuperseded",
            tenant_id=str(TENANT),
            release_id=str(RELEASE_ID),
            superseded_by_release_id=str(SUCCESSOR_ID),
            actor="eve",
            superseded_at="2026-10-09T00:03:00+00:00",
        ),
        _notification(
            "CodebookChangeProposed",
            tenant_id=str(TENANT),
            release_id=str(RELEASE_ID),
            successor_release_id=str(SUCCESSOR_ID),
            codebook_id=str(PARENT_ID),
            profile="team-codebook",
            summary="split the cost code",
            proposed_by="frank",
            proposed_at="2026-10-09T00:04:00+00:00",
            successor_manifest_json=MANIFEST,
            successor_manifest_sha256="b" * 64,
        ),
    ]


async def _project(calls_out: list[tuple[str, tuple[object, ...]]]) -> None:
    fake = FakeConn(results=["UPDATE 1", "UPDATE 1", "UPDATE 1"])
    projector = ReleaseProjector(MagicMock())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "corpus_kb.projections.research.release_projector.tenant_connection",
            _conn_patch(fake),
        )
        for notification in _lifecycle_notifications():
            await projector.handle(notification)
    calls_out.extend(fake.calls)


@pytest.mark.asyncio
async def test_replay_rebuilds_identical_rows_and_manifest_hash():
    first: list[tuple[str, tuple[object, ...]]] = []
    second: list[tuple[str, tuple[object, ...]]] = []
    await _project(first)
    await _project(second)
    assert first == second
    sqls = [sql for sql, _ in first]
    assert SQL_INSERT_RELEASE in sqls
    assert SQL_UPSERT_GATE in sqls
    assert SQL_INSERT_WAIVER in sqls
    assert SQL_RELEASE in sqls
    assert SQL_SUPERSEDE in sqls
    # The canonical manifest json is byte-stable across replays:
    manifest_args = [args for sql, args in first if sql == SQL_INSERT_RELEASE]
    assert len(manifest_args) == 2  # parent release + successor draft
    assert manifest_args[0][8] == manifest_args[1][8]
    import json

    stored = json.loads(str(manifest_args[0][8]))
    assert stored == MANIFEST


@pytest.mark.asyncio
async def test_non_release_topics_are_ignored():
    fake = FakeConn()
    projector = ReleaseProjector(MagicMock())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "corpus_kb.projections.research.release_projector.tenant_connection",
            _conn_patch(fake),
        )
        await projector.handle(_notification("CodeAdded", tenant_id=str(TENANT), codes=[]))
        await projector.handle(
            _notification("ReviewerRubricRecorded", tenant_id=str(TENANT), proposal_id="p")
        )
    assert fake.calls == []


def test_guarded_transitions_raise_on_zero_rows():
    payload = {"release_id": str(RELEASE_ID)}
    with pytest.raises(RuntimeError, match="zero rows"):
        ReleaseProjector._require_transition("UPDATE 0", "CodebookReleased", payload)
    ReleaseProjector._require_transition("UPDATE 1", "CodebookReleased", payload)
    with pytest.raises(RuntimeError, match="zero rows"):
        ReleaseProjector._require_transition("UPDATE 0", "CodebookRetired", payload)
    with pytest.raises(RuntimeError, match="zero rows"):
        ReleaseProjector._require_transition("UPDATE 0", "CodebookSuperseded", payload)
