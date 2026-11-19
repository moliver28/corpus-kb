"""Graph batch ops: ``batch_add_entities`` / ``batch_add_relations`` parity.

The GraphStore ABC ships one default batch implementation per op: a loop
over ``add_entity`` / ``add_relation`` (``storage/graph_store.py``). No
backend overrides the loops today, so the contract this module pins is
the LOOP's: same ids, same order, same upsert (duplicate) behavior, and
an empty batch writes nothing. The DuckDB store proves the contract on
the real backend; the existing in-memory fake (``test_graph_wiring``)
proves the ABC default every other backend inherits.

Perf (a set-based override) is out of scope here: these tests pin
semantics, not speed.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_store import DuckDbGraphStore
from corpus_kb.utils.models import Entity, Relation

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture()
async def migrated(duckdb_path: Any) -> Any:
    run_duckdb_migrations(f"duckdb:///{duckdb_path.as_posix()}")
    gateway = await DuckDbGateway.connect(f"duckdb:///{duckdb_path.as_posix()}")
    yield gateway
    await gateway.close()


@pytest.fixture()
async def duck_store(migrated: Any) -> DuckDbGraphStore:
    return DuckDbGraphStore(migrated.duplicate_connection(), tenant_id=TENANT)


def _entity(name: str, entity_type: str = "concept") -> Entity:
    return Entity(
        entity_id=str(uuid4()),
        name=name,
        entity_type=entity_type,
        source_type="text",
        metadata={},
    )


def _relation(source_id: str, target_id: str, relation_type: str = "related_to") -> Relation:
    return Relation(
        relation_id=str(uuid4()),
        source_entity_id=source_id,
        target_entity_id=target_id,
        relation_type=relation_type,
        weight=1.0,
        metadata={},
    )


def _keyed(source_id: str, target_id: str, chunk_id: str) -> Relation:
    """One chunk-keyed relation (the upsert dedupe key includes the chunk)."""
    relation = _relation(source_id, target_id, "cites")
    relation.chunk_id = chunk_id
    return relation


async def test_duckdb_batch_entities_match_the_add_loop(duck_store: DuckDbGraphStore) -> None:
    """The batch answers the same ids a manual add loop answers.

    The loop runs first, the batch re-adds the SAME entities: the upsert
    contract resolves every duplicate to the id already stored, so both
    orders answer one id per (tenant, name, type) and the table holds no
    duplicate rows.
    """
    entities = [_entity("alpha"), _entity("beta"), _entity("gamma")]
    looped = [await duck_store.add_entity(entity) for entity in entities]
    batched = await duck_store.batch_add_entities(entities)
    assert batched == looped
    rows = await duck_store._fetch("SELECT entity_id FROM entities", [])
    assert len(rows) == 3
    stored = sorted(str(row["entity_id"]) for row in rows)
    assert stored == sorted(looped)


async def test_duckdb_batch_relations_match_the_add_loop(duck_store: DuckDbGraphStore) -> None:
    """Relation batches ride the same loop contract, duplicates included.

    Two cases pin the shared unique-key semantics. NULL-chunk rows stay
    DISTINCT (the unique index keeps NULLs apart, on Postgres and DuckDB
    alike), so equal triples with no chunk always insert. A chunk-keyed
    triple dedupes: every later add resolves to the stored id, and the
    batch answers exactly the loop's ids.
    """
    alpha = await duck_store.add_entity(_entity("alpha"))
    beta = await duck_store.add_entity(_entity("beta"))

    # NULL chunk_id: equal triples stay distinct rows on both paths.
    looped = [
        await duck_store.add_relation(_relation(alpha, beta, "uses")),
        await duck_store.add_relation(_relation(alpha, beta, "uses")),
    ]
    assert looped[0] != looped[1]
    batched = await duck_store.batch_add_relations(
        [_relation(alpha, beta, "uses"), _relation(alpha, beta, "uses")]
    )
    assert batched[0] != batched[1]
    rows = await duck_store._fetch(
        "SELECT relation_id FROM relations WHERE relation_type = 'uses'", []
    )
    assert len(rows) == 4  # two loop rows + two batch rows

    # Chunk-keyed: the upsert contract collapses the repeat to one row.
    from corpus_kb.utils.models import Chunk, Document

    doc_id = await duck_store.add_document(
        Document(path="batch-ops-doc", content="", source_type="text", chunk_count=1, size_bytes=1)
    )
    chunk_id = await duck_store.add_chunk(
        Chunk(document_id=doc_id, text="batch ops chunk", source_type="text")
    )
    keyed_loop = [
        await duck_store.add_relation(_keyed(alpha, beta, str(chunk_id))),
        await duck_store.add_relation(_keyed(alpha, beta, str(chunk_id))),
    ]
    assert keyed_loop[0] == keyed_loop[1]
    keyed_batch = await duck_store.batch_add_relations(
        [_keyed(alpha, beta, str(chunk_id)), _keyed(alpha, beta, str(chunk_id))]
    )
    assert keyed_batch == keyed_loop
    cite_rows = await duck_store._fetch(
        "SELECT relation_id FROM relations WHERE relation_type = 'cites'", []
    )
    assert len(cite_rows) == 1


async def test_duckdb_batch_empty_and_mixed_duplicates(duck_store: DuckDbGraphStore) -> None:
    """An empty batch writes nothing; in-batch duplicates collapse.

    Feeding one entity twice in ONE batch mirrors two loop turns: the
    first inserts, the second resolves to the stored id, and the ids come
    back equal in entry order.
    """
    assert await duck_store.batch_add_entities([]) == []
    assert await duck_store.batch_add_relations([]) == []
    entity = _entity("dup")
    ids = await duck_store.batch_add_entities([entity, entity])
    assert ids[0] == ids[1]
    rows = await duck_store._fetch("SELECT entity_id FROM entities", [])
    assert len(rows) == 1


async def test_fake_graph_store_batch_matches_its_add_loop() -> None:
    """The ABC default batch on the existing fake: echo ids in order.

    ``FakeGraphStore`` (``test_graph_wiring``) answers each add with the
    entity's own id, so the batch loop must echo the same ids in entry
    order, collapse nothing, and answer [] for an empty batch.
    """
    from test_graph_wiring import FakeGraphStore

    store = FakeGraphStore()
    alpha = _entity("alpha")
    beta = _entity("beta")
    looped = [await store.add_entity(alpha), await store.add_entity(beta)]
    assert await store.batch_add_entities([alpha, beta]) == looped
    assert await store.batch_add_entities([]) == []

    relation = _relation(alpha.entity_id, beta.entity_id)
    relation_loop = await store.add_relation(relation)
    assert await store.batch_add_relations([relation]) == [relation_loop]
    assert await store.batch_add_relations([]) == []
