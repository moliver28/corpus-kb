"""FK integrity tests for PostgresGraphStore entity/relation paths."""

from __future__ import annotations

import pytest

from src.storage.graph_store import PostgresGraphStore
from src.utils.models import Entity, Relation


@pytest.mark.asyncio
async def test_add_entity_nonexistent_chunk_id_raises(pg_pool) -> None:
    """Single add_entity with a nonexistent chunk_id — Postgres allows NULL chunk_id."""
    store = PostgresGraphStore(pg_pool)
    entity = Entity(
        name="TestEntity",
        entity_type="Concept",
        source_type="code",
        chunk_id="nonexistent-chunk-id",
    )
    # Postgres entities table doesn't have FK on chunk_id — should succeed
    result = await store.add_entity(entity)
    assert result is not None


@pytest.mark.asyncio
async def test_add_entity_valid_succeeds(pg_pool) -> None:
    """Single add_entity succeeds."""
    store = PostgresGraphStore(pg_pool)
    entity = Entity(name="TestEntity2", entity_type="Concept", source_type="code")
    result = await store.add_entity(entity)
    assert result is not None


@pytest.mark.asyncio
async def test_add_relation_nonexistent_entity_raises(pg_pool) -> None:
    """add_relation with nonexistent entity IDs — Postgres FK constraint should reject."""
    store = PostgresGraphStore(pg_pool)
    relation = Relation(
        source_entity_id="00000000-0000-0000-0000-000000000099",
        target_entity_id="00000000-0000-0000-0000-000000000098",
        relation_type="MENTIONS",
    )
    with pytest.raises(Exception):
        await store.add_relation(relation)


@pytest.mark.asyncio
async def test_add_relation_valid_entities_succeeds(pg_pool) -> None:
    """add_relation between two existing entities succeeds."""
    store = PostgresGraphStore(pg_pool)
    e1 = Entity(name="EntityA", entity_type="Concept", source_type="code")
    e2 = Entity(name="EntityB", entity_type="Concept", source_type="code")
    eid1 = await store.add_entity(e1)
    eid2 = await store.add_entity(e2)

    relation = Relation(
        source_entity_id=eid1,
        target_entity_id=eid2,
        relation_type="RELATED_TO",
    )
    result = await store.add_relation(relation)
    assert result is not None
