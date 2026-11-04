"""Regression test for the double-promotion race (PR #63, commit e120372).

Two humans promote one proposal at the same moment. Both calls read the
proposal row before either mark commits, so both see status ``proposed``
and both mint a version. The fix adds a conditional predicate to the mark
step (``AND status = $7``) plus a rowcount check: only the racer whose
update still matches the live ``proposed`` row wins; the loser gets zero
updated rows and an error result instead of a second promotion.

The test models that interleaving with the shared in-memory gateway:
reads answer from a fixed stale snapshot, while the mark update applies
to a live row status. A mutant that drops the predicate or ignores the
rowcount makes both racers win, which fails the one-winner assertion.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest

from corpus_kb.research.promote_code import (
    DerivedPrototypes,
    _mark_proposal,
    promote_proposal,
)
from tests.mock_pg import FakeSqlGateway

SNAPSHOT_ROW: dict[str, Any] = {
    "proposed_id": 42,
    "cluster_id": "11111111-1111-1111-1111-111111111111",
    "run_id": "22222222-2222-2222-2222-222222222222",
    "batch_no": 1,
    "suggested_label": "Theme Observation",
    "member_unit_ids": [101, 102, 103],
    "n_members": 3,
    "dbcv_relative_validity": 0.4,
    "status": "proposed",
}

VIEW_ROWS: list[dict[str, Any]] = [
    {"unit_id": 101, "a_vec": [1.0, 0.0], "qa_vec": [1.0, 0.0], "q_vec": [1.0, 0.0]},
    {"unit_id": 102, "a_vec": [0.0, 1.0], "qa_vec": [0.0, 1.0], "q_vec": [0.0, 1.0]},
    {"unit_id": 103, "a_vec": [0.6, 0.8], "qa_vec": [0.6, 0.8], "q_vec": [0.6, 0.8]},
]

MARK_PREDICATE = "AND status = $7"


class RacedProposalGateway(FakeSqlGateway):
    """Gateway whose mark update applies to a live row status.

    Reads stay canned (both racers see the same stale snapshot). The mark
    update wins only when its SQL carries the from-status predicate and the
    live status still matches the expected one. Without the predicate a
    mutant matches the row anyway, so every racer flips the row and wins.
    """

    def __init__(self, results: dict[str, Any] | None = None) -> None:
        super().__init__(results)
        self.live_status = "proposed"
        self.wins = 0

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        canned = self._lookup(sql)
        if isinstance(canned, str):
            return canned
        expected = str(args[6]) if len(args) > 6 else None
        # The WHERE clause decides which rows match, not the argument count:
        # a mark without the status predicate matches the row unconditionally.
        guarded = MARK_PREDICATE in sql and expected is not None
        if guarded and self.live_status != expected:
            return "UPDATE 0"
        self.live_status = str(args[2])
        self.wins += 1
        return "UPDATE 1"


def _raced_gateway() -> RacedProposalGateway:
    return RacedProposalGateway(
        {
            "FROM research_proposed_codes": [dict(SNAPSHOT_ROW)],
            "JOIN research_exchanges": [dict(row) for row in VIEW_ROWS],
            "COUNT(*) FROM codebook_versions": [{"n_versions": 2}],
        }
    )


@pytest.fixture()
def mint_log(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace version minting with a recorder (the race is in the mark step)."""
    minted: list[dict[str, Any]] = []

    async def record_mint(
        pool: asyncpg.Pool,
        handler: Any,
        tenant_id: UUID,
        name: str,
        definition: str,
        inclusion: str,
        exclusion: str,
        derived: DerivedPrototypes,
        label: str,
    ) -> dict[str, str]:
        minted.append({"name": name, "label": label, "n_members": len(derived.unit_ids)})
        suffix = f"{len(minted):012d}"
        return {"version_id": f"00000000-0000-0000-0000-{suffix}", "code_id": f"code-{len(minted)}"}

    monkeypatch.setattr("corpus_kb.research.promote_code._mint_version", record_mint)
    return minted


@pytest.mark.asyncio
async def test_mark_guard_requires_status_and_reports_rowcount() -> None:
    gateway = FakeSqlGateway({MARK_PREDICATE: "UPDATE 0"})
    marked = await _mark_proposal(
        gateway,
        uuid4(),
        42,
        status="promoted",
        promoted_version_id="v-1",
        promoted_code_id="c-1",
        from_status="proposed",
    )
    assert marked == 0
    sql, args = gateway.calls[-1]
    assert MARK_PREDICATE in sql
    assert len(args) == 7
    assert args[6] == "proposed"

    gateway.results = {MARK_PREDICATE: "UPDATE 1"}
    marked = await _mark_proposal(
        gateway,
        uuid4(),
        42,
        status="promoted",
        promoted_version_id="v-1",
        promoted_code_id="c-1",
        from_status="proposed",
    )
    assert marked == 1


@pytest.mark.asyncio
async def test_mark_without_guard_predicate_stays_unconditional() -> None:
    gateway = FakeSqlGateway()
    marked = await _mark_proposal(gateway, uuid4(), 42, status="duplicate_blocked")
    assert marked == 1  # default status tag for an unconditional update
    sql, args = gateway.calls[-1]
    assert MARK_PREDICATE not in sql
    assert len(args) == 6


@pytest.mark.asyncio
async def test_second_racer_errors_and_exactly_one_promotion_lands(
    mint_log: list[dict[str, Any]],
) -> None:
    gateway = _raced_gateway()
    winner = await promote_proposal(gateway, uuid4(), 42, "Theme", "Observed units")
    loser = await promote_proposal(gateway, uuid4(), 42, "Theme", "Observed units")

    assert winner["status"] == "promoted", winner
    assert loser["status"] == "error", loser
    assert "concurrently promoted" in loser["reason"]
    assert len(mint_log) == 2  # both racers minted; the mark step decided the winner
    assert gateway.wins == 1  # the live row flipped exactly once
    assert gateway.live_status == "promoted"
