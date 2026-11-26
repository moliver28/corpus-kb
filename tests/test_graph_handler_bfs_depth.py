"""The handler gates bfs max_depth uniformly for every store (#527 P3).

The AGE store validated 1..25 but the other stores never validated, so a
bad depth reached the SQL layer. The handler now raises the taxonomy's
ValidationError before any store call.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from corpus_kb.errors import ValidationError
from corpus_kb.handlers.graph_handler import BFS_MAX_DEPTH, GraphHandler
from corpus_kb.utils.models import Entity

TENANT = UUID("00000000-0000-0000-0000-000000000001")
START = UUID("00000000-0000-0000-0000-000000000002")


class _RecordingStore:
    """GraphStore double: records bfs depths, answers one entity."""

    def __init__(self) -> None:
        self.bfs_depths: list[int] = []

    async def bfs(self, start_entity_id: str, max_depth: int) -> dict[str, object]:
        self.bfs_depths.append(max_depth)
        return {"visited": {str(START): 0}}

    async def get_entity(self, entity_id: str) -> Entity:
        return Entity(
            entity_id=entity_id,
            name="root",
            entity_type="concept",
            source_type="code",
        )


async def test_bfs_rejects_depth_zero() -> None:
    store = _RecordingStore()
    handler = GraphHandler(store)
    with pytest.raises(ValidationError):
        await handler.handle_bfs(TENANT, START, max_depth=0)
    assert store.bfs_depths == []


async def test_bfs_rejects_depth_over_cap() -> None:
    store = _RecordingStore()
    handler = GraphHandler(store)
    with pytest.raises(ValidationError):
        await handler.handle_bfs(TENANT, START, max_depth=BFS_MAX_DEPTH + 1)
    assert store.bfs_depths == []


async def test_bfs_accepts_depth_one() -> None:
    store = _RecordingStore()
    handler = GraphHandler(store)
    rows = await handler.handle_bfs(TENANT, START, max_depth=1)
    assert store.bfs_depths == [1]
    assert rows[0]["entity_id"] == str(START)
    assert rows[0]["depth"] == 0
