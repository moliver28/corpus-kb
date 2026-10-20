"""Golden store parity: one scenario, both graph stores, same envelopes.

The storage-portability program's per-store proof (§12 — the full-parity
harness generalizes this file): the SAME diamond entity/relation scenario
runs against ``PostgresGraphStore`` (live local Postgres via the env-derived
research DSN; the ``requires_postgres`` legs auto-skip without it) and
``DuckDbGraphStore`` (hermetic tmp file carrying the real
``migrations_duckdb`` schema). Every leg's envelope is byte-compared, with
the total orders the stores must agree on pinned in the golden builder:

  - neighbors/traverse rows: ``(depth, name, entity_id)`` (shared
    ``graph_sql`` builders — drift is structurally impossible here, and
    this file proves it stays that way end to end),
  - ``search_entities``: ``(name, entity_id)``,
  - ``get_entity_relations``: ``relation_id``.

Windows hygiene (program rule 13): the DuckDB connection closes before the
tmp file could ever be removed; the PG pool closes after the store.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from corpus_kb.storage.graph_store import GraphStore, PostgresGraphStore
from corpus_kb.utils.models import Entity, Relation

# --- The shared scenario ----------------------------------------------------
# Diamond: a->b->d, a->c->d, plus the back edge d->a (cycle-guard food).
# One non-default weight (0.75 is exact in binary floating point) and one
# single-key metadata dict (JSONB and JSON cannot disagree on one key).
#
# Identifiers derive from a per-leg fragment: entity/relation primary keys
# are GLOBAL (not per-tenant) on both engines, so the two Postgres legs of
# one session must never re-seed the same ids — and the stores' relation
# upsert cannot dedupe a NULL-chunk triple (ON CONFLICT never matches a
# NULL index key), so each leg also seeds its own tenant. That upsert hole
# is a graph-store contract finding, not a harness concern.


@dataclass(frozen=True)
class ScenarioIds:
    """The scenario's fixed-shape identifiers for one leg."""

    tenant: str
    a: str
    b: str
    c: str
    d: str
    r1: str
    r2: str
    r3: str
    r4: str
    r5: str


def _leg_ids() -> ScenarioIds:
    """Fresh scenario ids: the 12-char id tail starts with 8 fresh hex chars."""
    leg = uuid4().hex[:8]
    return ScenarioIds(
        tenant=str(uuid4()),
        a=f"00000000-0000-0000-0000-{leg}0001",
        b=f"00000000-0000-0000-0000-{leg}0002",
        c=f"00000000-0000-0000-0000-{leg}0003",
        d=f"00000000-0000-0000-0000-{leg}0004",
        r1=f"00000000-0000-0000-0000-{leg}0101",
        r2=f"00000000-0000-0000-0000-{leg}0102",
        r3=f"00000000-0000-0000-0000-{leg}0103",
        r4=f"00000000-0000-0000-0000-{leg}0104",
        r5=f"00000000-0000-0000-0000-{leg}0105",
    )


_ENTITIES: list[tuple[str, str, dict[str, object]]] = [
    ("a", "A-Node", "concept", {"lang": "py"}),
    ("b", "B-Node", "concept", {}),
    ("c", "C-Node", "idea", {}),
    ("d", "D-Node", "concept", {}),
]

_RELATIONS: list[tuple[str, str, str, float]] = [
    ("r1", "a", "b", "kinds", 1.0),
    ("r2", "a", "c", "other", 1.0),
    ("r3", "b", "d", "kinds", 0.75),
    ("r4", "c", "d", "other", 1.0),
    ("r5", "d", "a", "kinds", 1.0),
]


def _entity(entity_id: str, name: str, entity_type: str, metadata: dict[str, object]) -> Entity:
    return Entity(
        entity_id=entity_id,
        name=name,
        entity_type=entity_type,
        source_type="code",
        metadata=metadata,
    )


def _relation(
    relation_id: str, source: str, target: str, relation_type: str, weight: float
) -> Relation:
    return Relation(
        relation_id=relation_id,
        source_entity_id=source,
        target_entity_id=target,
        relation_type=relation_type,
        weight=weight,
    )


# --- The golden builder -------------------------------------------------------
# Row order IS the pin: the stores must return rows in exactly this order.


def _walk_row(entity_id: str, name: str, entity_type: str, depth: int) -> dict[str, object]:
    return {
        "entity_id": entity_id,
        "name": name,
        "entity_type": entity_type,
        "metadata": {},
        "depth": depth,
    }


def _entity_render(entity: Entity) -> dict[str, object]:
    return {
        "entity_id": entity.entity_id,
        "name": entity.name,
        "entity_type": entity.entity_type,
        "source_document_id": entity.source_document_id,
        "metadata": entity.metadata,
    }


def _relation_render(relation: Relation) -> dict[str, object]:
    return {
        "relation_id": relation.relation_id,
        "source_entity_id": relation.source_entity_id,
        "target_entity_id": relation.target_entity_id,
        "relation_type": relation.relation_type,
        "weight": relation.weight,
        "metadata": relation.metadata,
    }


def _golden(ids: ScenarioIds) -> dict[str, object]:
    """The expected envelope for ids: pinned rows, canonicalized later."""
    return {
        # The upsert contract returns the caller's ids on a fresh store.
        "add_entity_ids": [ids.a, ids.b, ids.c, ids.d],
        "neighbors_out_h1": [
            _walk_row(ids.b, "B-Node", "concept", 1),
            _walk_row(ids.c, "C-Node", "idea", 1),
        ],
        "neighbors_out_h2_kinds": [
            _walk_row(ids.b, "B-Node", "concept", 1),
            _walk_row(ids.d, "D-Node", "concept", 2),
        ],
        "neighbors_in_h1": [
            _walk_row(ids.b, "B-Node", "concept", 1),
            _walk_row(ids.c, "C-Node", "idea", 1),
        ],
        # The back edge d->a makes D a depth-1 UNDIRECTED neighbor of A.
        "neighbors_both_h2": [
            _walk_row(ids.b, "B-Node", "concept", 1),
            _walk_row(ids.c, "C-Node", "idea", 1),
            _walk_row(ids.d, "D-Node", "concept", 1),
        ],
        "traverse_d2": [
            _walk_row(ids.b, "B-Node", "concept", 1),
            _walk_row(ids.c, "C-Node", "idea", 1),
            _walk_row(ids.d, "D-Node", "concept", 1),
        ],
        "traverse_d5_other": [
            _walk_row(ids.c, "C-Node", "idea", 1),
            _walk_row(ids.d, "D-Node", "concept", 2),
        ],
        "search_all": [
            _entity_render(_entity(ids.a, "A-Node", "concept", {"lang": "py"})),
            _entity_render(_entity(ids.b, "B-Node", "concept", {})),
            _entity_render(_entity(ids.c, "C-Node", "idea", {})),
            _entity_render(_entity(ids.d, "D-Node", "concept", {})),
        ],
        "search_typed_idea": [_entity_render(_entity(ids.c, "C-Node", "idea", {}))],
        "relations_of_b": [
            {
                "relation_id": ids.r1,
                "source_entity_id": ids.a,
                "target_entity_id": ids.b,
                "relation_type": "kinds",
                "weight": 1.0,
                "metadata": {},
            },
            {
                "relation_id": ids.r3,
                "source_entity_id": ids.b,
                "target_entity_id": ids.d,
                "relation_type": "kinds",
                "weight": 0.75,
                "metadata": {},
            },
        ],
        "get_entity_b": {
            "entity_id": ids.b,
            "name": "B-Node",
            "entity_type": "concept",
            "source_document_id": None,
            "metadata": {},
        },
    }


# NOTE: ``bfs`` is deliberately absent from the scenario. It is the
# deprecated op (the first-class neighbors/traverse pair replaced it), and
# the Postgres store's bfs SQL carries a pre-existing prepare-time defect
# (uuid = text in its CASE branch). A fix belongs to the graph-store
# owner, not to this parity harness.


# --- The scenario runner ------------------------------------------------------


async def _seed(store: GraphStore, ids: ScenarioIds) -> list[str]:
    seeded: list[str] = []
    for key, name, entity_type, metadata in _ENTITIES:
        entity_id = getattr(ids, key)
        seeded.append(await store.add_entity(_entity(entity_id, name, entity_type, metadata)))
    for key, source, target, relation_type, weight in _RELATIONS:
        await store.add_relation(
            _relation(
                getattr(ids, key),
                getattr(ids, source),
                getattr(ids, target),
                relation_type,
                weight,
            )
        )
    return seeded


async def _run_scenario(store: GraphStore, ids: ScenarioIds) -> dict[str, object]:
    added = await _seed(store, ids)
    entity_b = await store.get_entity(ids.b)
    return {
        "add_entity_ids": added,
        "neighbors_out_h1": await store.neighbors(ids.a, hops=1, direction="out"),
        "neighbors_out_h2_kinds": await store.neighbors(
            ids.a, hops=2, direction="out", relation_types=["kinds"]
        ),
        "neighbors_in_h1": await store.neighbors(ids.d, hops=1, direction="in"),
        "neighbors_both_h2": await store.neighbors(ids.a, hops=2, direction="both"),
        "traverse_d2": await store.traverse(ids.a, max_depth=2),
        "traverse_d5_other": await store.traverse(ids.a, max_depth=5, relation_types=["other"]),
        "search_all": [_entity_render(entity) for entity in await store.search_entities("Node")],
        "search_typed_idea": [
            _entity_render(entity)
            for entity in await store.search_entities("Node", entity_type="idea")
        ],
        "relations_of_b": [
            _relation_render(relation) for relation in await store.get_entity_relations(ids.b)
        ],
        "get_entity_b": _entity_render(entity_b) if entity_b else None,
    }


def _envelope(envelope: dict[str, object]) -> str:
    """Byte-stable rendering: pinned row order, canonicalized key order."""
    return json.dumps(envelope, sort_keys=True)


# --- The legs -------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_postgres_store_leg_matches_golden(research_dsn: str) -> None:
    """The live Postgres store reproduces the golden envelope exactly."""
    ids = _leg_ids()
    pool = await asyncpg.create_pool(research_dsn, min_size=1, max_size=2)
    store = PostgresGraphStore(pool, tenant_id=ids.tenant)
    try:
        envelope = await _run_scenario(store, ids)
    finally:
        await store.close()
        await pool.close()
    assert _envelope(envelope) == _envelope(_golden(ids))


@pytest.mark.asyncio
async def test_duckdb_store_leg_matches_golden(tmp_path: Path) -> None:
    """The hermetic DuckDB store (real migrations) reproduces the golden."""
    duckdb = pytest.importorskip("duckdb")
    from corpus_kb._setup.migrate import run_migrations
    from corpus_kb.storage.duckdb_store import DuckDbGraphStore
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

    ids = _leg_ids()
    path = tmp_path / "parity.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    conn = duckdb.connect(str(path))
    store = DuckDbGraphStore(conn, tenant_id=ids.tenant)
    try:
        envelope = await _run_scenario(store, ids)
    finally:
        await store.close()
        conn.close()
    assert _envelope(envelope) == _envelope(_golden(ids))


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_stores_match_byte_for_byte(research_dsn: str, tmp_path: Path) -> None:
    """Both stores run the same scenario; the envelopes are byte-identical."""
    duckdb = pytest.importorskip("duckdb")
    from corpus_kb._setup.migrate import run_migrations
    from corpus_kb.storage.duckdb_store import DuckDbGraphStore
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

    pg_ids = _leg_ids()
    pool = await asyncpg.create_pool(research_dsn, min_size=1, max_size=2)
    pg_store = PostgresGraphStore(pool, tenant_id=pg_ids.tenant)
    try:
        pg_envelope = await _run_scenario(pg_store, pg_ids)
    finally:
        await pg_store.close()
        await pool.close()

    duck_ids = _leg_ids()
    path = tmp_path / "parity.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    conn = duckdb.connect(str(path))
    duck_store = DuckDbGraphStore(conn, tenant_id=duck_ids.tenant)
    try:
        duck_envelope = await _run_scenario(duck_store, duck_ids)
    finally:
        await duck_store.close()
        conn.close()

    assert _envelope(pg_envelope) == _envelope(_golden(pg_ids))
    assert _envelope(duck_envelope) == _envelope(_golden(duck_ids))
    # The cross-backend proof: identical shapes, ids normalized away.
    assert _normalize(pg_envelope, pg_ids) == _normalize(duck_envelope, duck_ids)


def _normalize(envelope: dict[str, object], ids: ScenarioIds) -> str:
    """Envelope rendering with this leg's ids masked, for cross-backend diffing."""
    text = _envelope(envelope)
    for key in ("a", "b", "c", "d", "r1", "r2", "r3", "r4", "r5"):
        text = text.replace(getattr(ids, key), f"<{key}>")
    return text.replace(ids.tenant, "<tenant>")
