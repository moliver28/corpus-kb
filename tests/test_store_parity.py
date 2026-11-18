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
from uuid import UUID, uuid4

import asyncpg
import pytest

from corpus_kb.storage.gateway import SqlGateway
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
    """The scenario's fixed-shape identifiers for one leg.

    ``alias`` is the id a duplicate ``add-entity`` returns: a NEW aggregate
    uuid the CLI hands back after the projection deduped the row. The
    projection aliases it to the canonical row (issue #455), and every
    graph read must answer it (issue #492).
    """

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
    alias: str


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
        alias=str(uuid4()),
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
    relation_id: str,
    source: str,
    target: str,
    relation_type: str,
    weight: float,
    confidence: float | None = None,
) -> Relation:
    return Relation(
        relation_id=relation_id,
        source_entity_id=source,
        target_entity_id=target,
        relation_type=relation_type,
        weight=weight,
        confidence=confidence,
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
        "bfs_d2": {
            "start_entity_id": ids.a,
            "max_depth": 2,
            "visited": {ids.a: 0, ids.b: 1, ids.c: 1, ids.d: 1},
        },
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


# ``bfs`` joined the scenario when the Postgres store's CASE-branch type
# defect was fixed (it compared uuid = text and failed at prepare time;
# issue #492's alias parity needed the hop answer on both engines). It
# stays the deprecated op — the first-class neighbors/traverse pair
# replaced it — but it must still answer identically while it exists.


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
        "bfs_d2": await store.bfs(ids.a, max_depth=2),
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


# --- The alias-id scenario (issues #455/#492) -----------------------------------


async def _project_duplicate_add(gateway: SqlGateway, ids: ScenarioIds) -> None:
    """Run the shared projection's half of a duplicate ``add-entity``.

    ``CommandHandler.handle_add_entity`` mints a NEW aggregate uuid and the
    CLI returns it; ``project_entity`` (engine-agnostic, the ONE writer)
    then dedupes on (tenant, name, type) and aliases that uuid to the
    canonical row. This drives exactly that shared function on each leg.
    """
    from corpus_kb.projections.documents_rows import project_entity

    await project_entity(
        gateway,
        UUID(ids.tenant),
        {
            "aggregate_id": ids.alias,
            "name": "A-Node",
            "entity_type": "concept",
            "metadata": {},
        },
    )


async def _run_alias_scenario(store: GraphStore, ids: ScenarioIds) -> dict[str, object]:
    """Every read answers the CLI-returned alias id like the canonical row."""
    entity_a = await store.get_entity(ids.alias)
    return {
        "get_entity_a": _entity_render(entity_a) if entity_a else None,
        "relations_of_a": [
            _relation_render(relation) for relation in await store.get_entity_relations(ids.alias)
        ],
        "neighbors_out_h1": await store.neighbors(ids.alias, hops=1, direction="out"),
        "neighbors_both_h2": await store.neighbors(ids.alias, hops=2, direction="both"),
        "traverse_d2": await store.traverse(ids.alias, max_depth=2),
        "bfs_d2": await store.bfs(ids.alias, max_depth=2),
    }


def _alias_golden(ids: ScenarioIds) -> dict[str, object]:
    """a's view of the diamond, reached through the alias id."""
    return {
        "get_entity_a": _entity_render(_entity(ids.a, "A-Node", "concept", {"lang": "py"})),
        "relations_of_a": [
            {
                "relation_id": ids.r1,
                "source_entity_id": ids.a,
                "target_entity_id": ids.b,
                "relation_type": "kinds",
                "weight": 1.0,
                "metadata": {},
            },
            {
                "relation_id": ids.r2,
                "source_entity_id": ids.a,
                "target_entity_id": ids.c,
                "relation_type": "other",
                "weight": 1.0,
                "metadata": {},
            },
            {
                "relation_id": ids.r5,
                "source_entity_id": ids.d,
                "target_entity_id": ids.a,
                "relation_type": "kinds",
                "weight": 1.0,
                "metadata": {},
            },
        ],
        "neighbors_out_h1": [
            _walk_row(ids.b, "B-Node", "concept", 1),
            _walk_row(ids.c, "C-Node", "idea", 1),
        ],
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
        "bfs_d2": {
            "start_entity_id": ids.a,
            "max_depth": 2,
            "visited": {ids.a: 0, ids.b: 1, ids.c: 1, ids.d: 1},
        },
    }


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


# --- Alias-id parity (issues #455/#492) ------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_alias_id_reads_postgres_leg_matches_golden(research_dsn: str) -> None:
    """The live Postgres store answers a CLI-returned alias id with the
    canonical row — get_entity, relations, hops, walk and bfs all of it."""
    from corpus_kb.storage.gateway import PostgresGateway

    ids = _leg_ids()
    pool = await asyncpg.create_pool(research_dsn, min_size=1, max_size=2)
    store = PostgresGraphStore(pool, tenant_id=ids.tenant)
    try:
        await _seed(store, ids)
        await _project_duplicate_add(PostgresGateway(pool), ids)
        envelope = await _run_alias_scenario(store, ids)
    finally:
        await store.close()
        await pool.close()
    assert _envelope(envelope) == _envelope(_alias_golden(ids))


@pytest.mark.asyncio
async def test_alias_id_reads_duckdb_leg_matches_golden(tmp_path: Path) -> None:
    """The hermetic DuckDB store keeps answering alias ids (the #455 half)."""
    pytest.importorskip("duckdb")
    from corpus_kb._setup.migrate import run_migrations
    from corpus_kb.storage.duckdb_gateway import DuckDbGateway
    from corpus_kb.storage.duckdb_store import DuckDbGraphStore
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

    ids = _leg_ids()
    dsn = duckdb_path_to_conn_string(tmp_path / "alias-parity.duckdb")
    await run_migrations(dsn)
    gateway = await DuckDbGateway.connect(dsn)
    store = DuckDbGraphStore(gateway.duplicate_connection(), tenant_id=ids.tenant)
    try:
        await _seed(store, ids)
        await _project_duplicate_add(gateway, ids)
        envelope = await _run_alias_scenario(store, ids)
    finally:
        await store.close()
        await gateway.close()
    assert _envelope(envelope) == _envelope(_alias_golden(ids))


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_alias_reads_match_byte_for_byte(research_dsn: str, tmp_path: Path) -> None:
    """The SAME duplicate-add flow lands the SAME alias rows on both engines,
    and the alias-id read envelopes are byte-identical after id masking."""
    pytest.importorskip("duckdb")
    from corpus_kb._setup.migrate import run_migrations
    from corpus_kb.storage.duckdb_gateway import DuckDbGateway
    from corpus_kb.storage.duckdb_store import DuckDbGraphStore
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
    from corpus_kb.storage.gateway import PostgresGateway

    pg_ids = _leg_ids()
    pool = await asyncpg.create_pool(research_dsn, min_size=1, max_size=2)
    pg_store = PostgresGraphStore(pool, tenant_id=pg_ids.tenant)
    try:
        await _seed(pg_store, pg_ids)
        await _project_duplicate_add(PostgresGateway(pool), pg_ids)
        pg_envelope = await _run_alias_scenario(pg_store, pg_ids)
    finally:
        await pg_store.close()
        await pool.close()

    duck_ids = _leg_ids()
    dsn = duckdb_path_to_conn_string(tmp_path / "alias-parity.duckdb")
    await run_migrations(dsn)
    gateway = await DuckDbGateway.connect(dsn)
    duck_store = DuckDbGraphStore(gateway.duplicate_connection(), tenant_id=duck_ids.tenant)
    try:
        await _seed(duck_store, duck_ids)
        await _project_duplicate_add(gateway, duck_ids)
        duck_envelope = await _run_alias_scenario(duck_store, duck_ids)
    finally:
        await duck_store.close()
        await gateway.close()

    assert _envelope(pg_envelope) == _envelope(_alias_golden(pg_ids))
    assert _envelope(duck_envelope) == _envelope(_alias_golden(duck_ids))
    assert _normalize(pg_envelope, pg_ids) == _normalize(duck_envelope, duck_ids)


# --- Relation provenance: confidence roundtrip ------------------------------------


@pytest.mark.asyncio
@pytest.mark.requires_postgres
async def test_add_relation_persists_confidence_identically(
    research_dsn: str, tmp_path: Path
) -> None:
    """add_relation keeps the given confidence on BOTH engines (migration
    008's column; the DuckDB insert used to omit it and store NULL)."""
    duckdb = pytest.importorskip("duckdb")
    from corpus_kb._setup.migrate import run_migrations
    from corpus_kb.storage.duckdb_store import DuckDbGraphStore
    from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
    from corpus_kb.storage.tenant_conn import tenant_connection

    ids = _leg_ids()
    pool = await asyncpg.create_pool(research_dsn, min_size=1, max_size=2)
    pg_store = PostgresGraphStore(pool, tenant_id=ids.tenant)
    try:
        await pg_store.add_entity(_entity(ids.a, "A-Node", "concept", {}))
        await pg_store.add_entity(_entity(ids.b, "B-Node", "concept", {}))
        await pg_store.add_relation(_relation(ids.r1, ids.a, ids.b, "kinds", 1.0, 0.75))
        async with tenant_connection(pool, ids.tenant) as conn:
            pg_confidence = await conn.fetchval(
                "SELECT confidence FROM relations WHERE relation_id = $1", UUID(ids.r1)
            )
    finally:
        await pg_store.close()
        await pool.close()

    path = tmp_path / "confidence-parity.duckdb"
    await run_migrations(duckdb_path_to_conn_string(path))
    conn = duckdb.connect(str(path))
    duck_store = DuckDbGraphStore(conn, tenant_id=ids.tenant)
    try:
        await duck_store.add_entity(_entity(ids.a, "A-Node", "concept", {}))
        await duck_store.add_entity(_entity(ids.b, "B-Node", "concept", {}))
        await duck_store.add_relation(_relation(ids.r1, ids.a, ids.b, "kinds", 1.0, 0.75))
        duck_confidence = conn.execute(
            "SELECT confidence FROM relations WHERE relation_id = ?", [ids.r1]
        ).fetchone()[0]
    finally:
        await duck_store.close()
        conn.close()

    assert pg_confidence == 0.75
    assert duck_confidence == 0.75


def _normalize(envelope: dict[str, object], ids: ScenarioIds) -> str:
    """Envelope rendering with this leg's ids masked, for cross-backend diffing."""
    text = _envelope(envelope)
    for key in ("a", "b", "c", "d", "r1", "r2", "r3", "r4", "r5", "alias"):
        text = text.replace(getattr(ids, key), f"<{key}>")
    return text.replace(ids.tenant, "<tenant>")
