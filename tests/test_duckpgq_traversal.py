"""DuckPGQ opt-in: CTE equivalence, one-shot fallback, factory gate.

Hermetic end to end on a real in-memory DuckDB. The equivalence fixture
is one small fixed graph (the diamond plus cycle and self-loop edges);
every traversal comes back through both engines and must be identical,
including the pinned ``(depth, name, entity_id)`` order.

The engine-gated tests skip honestly when the DuckPGQ community extension
cannot install or load on the running DuckDB build — the same convention
as the Ollama-gated tests. The fallback tests run in both worlds: the
engine-off contract (one typed warning, permanent CTE for that
connection) never depends on the extension being present.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import duckdb
import pytest

from corpus_kb.errors import DatabaseUnavailableError
from corpus_kb.storage import duckpgq
from corpus_kb.storage.duckdb_store import DuckDbGraphStore
from corpus_kb.storage.duckpgq import DuckPGQUnavailableError

TENANT = "00000000-0000-0000-0000-000000000001"

_DDL = (
    """
    CREATE TABLE entities (
        entity_id VARCHAR PRIMARY KEY,
        tenant_id VARCHAR NOT NULL,
        name VARCHAR NOT NULL,
        entity_type VARCHAR NOT NULL,
        source_document_id VARCHAR,
        metadata VARCHAR,
        UNIQUE (tenant_id, name, entity_type)
    )
    """,
    """
    CREATE TABLE relations (
        relation_id VARCHAR PRIMARY KEY,
        tenant_id VARCHAR NOT NULL,
        source_entity_id VARCHAR NOT NULL,
        target_entity_id VARCHAR NOT NULL,
        relation_type VARCHAR NOT NULL,
        weight DOUBLE,
        metadata VARCHAR,
        chunk_id VARCHAR,
        UNIQUE (tenant_id, source_entity_id, target_entity_id, relation_type, chunk_id)
    )
    """,
)

# Diamond a->b->d, a->c->d, the cycle d->a, and two self-loops: the CTE
# path guard and the engine's walks must agree on every row they return.
_SEED_ENTITIES = [
    ("a", "A-Node", "concept"),
    ("b", "B-Node", "concept"),
    ("c", "C-Node", "idea"),
    ("d", "D-Node", "concept"),
]
_SEED_RELATIONS = [
    ("r1", "a", "b", "kinds"),
    ("r2", "a", "c", "other"),
    ("r3", "b", "d", "kinds"),
    ("r4", "c", "d", "other"),
    ("r5", "d", "a", "kinds"),
    ("r6", "b", "b", "self"),
    ("r7", "a", "a", "self"),
]

_ENGINE_STATE: str | None = None


def _engine_available() -> str:
    """Probe install/load once per session: "yes", "no", or unknown."""
    global _ENGINE_STATE
    if _ENGINE_STATE is None:
        conn = duckdb.connect(":memory:")
        try:
            duckpgq.install_and_load(conn)
            _ENGINE_STATE = "yes"
        except Exception:
            _ENGINE_STATE = "no"
        finally:
            conn.close()
    return _ENGINE_STATE


def _skip_without_engine() -> None:
    if _engine_available() == "no":
        pytest.skip("DuckPGQ community extension unavailable on this DuckDB build")


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    for ddl in _DDL:
        conn.execute(ddl)
    for entity_id, name, entity_type in _SEED_ENTITIES:
        conn.execute(
            "INSERT INTO entities VALUES (?, ?, ?, ?, NULL, '{}')",
            [entity_id, TENANT, name, entity_type],
        )
    for relation_id, source, target, relation_type in _SEED_RELATIONS:
        conn.execute(
            "INSERT INTO relations VALUES (?, ?, ?, ?, ?, 1.0, '{}', NULL)",
            [relation_id, TENANT, source, target, relation_type],
        )


def _store(engine: str) -> DuckDbGraphStore:
    conn = duckdb.connect(":memory:", config={"default_null_order": "postgres"})
    _seed(conn)
    return DuckDbGraphStore(conn, tenant_id=TENANT, traversal_engine=engine)


@pytest.fixture
async def cte_store() -> AsyncIterator[DuckDbGraphStore]:
    store = _store("cte")
    yield store
    await store.close()


@pytest.fixture
async def pgq_store() -> AsyncIterator[DuckDbGraphStore]:
    _skip_without_engine()
    store = _store("duckpgq")
    yield store
    await store.close()


async def test_traverse_matches_cte(
    cte_store: DuckDbGraphStore,
    pgq_store: DuckDbGraphStore,
) -> None:
    """The undirected walk returns identical ordered rows on both engines."""
    cte_rows = await cte_store.traverse("a", 3)
    pgq_rows = await pgq_store.traverse("a", 3)
    assert pgq_rows == cte_rows
    # The cycle edge d->a makes d adjacent too; order pins (depth, name).
    assert [row["entity_id"] for row in pgq_rows] == ["b", "c", "d"]
    assert all(row["depth"] == 1 for row in pgq_rows)


async def test_traverse_from_middle_matches_cte(
    cte_store: DuckDbGraphStore,
    pgq_store: DuckDbGraphStore,
) -> None:
    """Depth windows and the start-row exclusion hold from any node."""
    for start in ("b", "d"):
        assert await pgq_store.traverse(start, 2) == await cte_store.traverse(start, 2)


async def test_neighbors_match_cte(
    cte_store: DuckDbGraphStore,
    pgq_store: DuckDbGraphStore,
) -> None:
    """All three directions at every fast-path hop depth agree."""
    for hops in (1, 2, 3):
        for direction in ("out", "in", "both"):
            assert await pgq_store.neighbors("a", hops, direction) == (
                await cte_store.neighbors("a", hops, direction)
            ), f"hops={hops} direction={direction}"


async def test_entity_type_filter_matches_cte(
    cte_store: DuckDbGraphStore,
    pgq_store: DuckDbGraphStore,
) -> None:
    """The vertex type filter narrows both engines identically."""
    assert await pgq_store.neighbors("a", 2, "both", entity_types=["concept"]) == (
        await cte_store.neighbors("a", 2, "both", entity_types=["concept"])
    )
    assert await pgq_store.neighbors("a", 2, "both", entity_types=["concept"]) != []


async def test_bfs_matches_cte(
    cte_store: DuckDbGraphStore,
    pgq_store: DuckDbGraphStore,
) -> None:
    """The visited map (start at zero, min depths) agrees on both engines."""
    assert await pgq_store.bfs("a", 3) == await cte_store.bfs("a", 3)
    missing = await pgq_store.bfs("zz", 2)
    assert missing == await cte_store.bfs("zz", 2)
    assert missing["visited"] == {}


async def test_relation_types_match_cte(
    cte_store: DuckDbGraphStore,
    pgq_store: DuckDbGraphStore,
) -> None:
    """The edge type filter narrows both engines identically (chains)."""
    for hops, direction in ((1, "both"), (2, "both"), (3, "out")):
        assert await pgq_store.neighbors("a", hops, direction, relation_types=["kinds"]) == (
            await cte_store.neighbors("a", hops, direction, relation_types=["kinds"])
        ), f"hops={hops} direction={direction}"


async def test_deep_walk_falls_back_and_warns_once(
    cte_store: DuckDbGraphStore,
    pgq_store: DuckDbGraphStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Past the 3-hop chain bound: warn once, then CTE forever."""
    caplog.set_level(logging.WARNING, logger="corpus_kb.storage.duckdb_store")
    assert await pgq_store.traverse("a", 5) == await cte_store.traverse("a", 5)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    # The branch is off for this connection: no new warning, CTE rows.
    assert await pgq_store.neighbors("a", 1, "out") == await cte_store.neighbors("a", 1, "out")
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


async def test_engine_probe_failure_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When the extension cannot load, the first walk warns once and degrades."""

    def _broken(conn: duckdb.DuckDBPyConnection) -> None:
        raise DuckPGQUnavailableError(
            "DuckPGQ traversal is not usable here; the store uses the CTE path.",
            remediation="Keep graph.duckdb_graph_backend at cte.",
        )

    monkeypatch.setattr("corpus_kb.storage.duckdb_store.ensure_duckpgq", _broken)
    caplog.set_level(logging.WARNING, logger="corpus_kb.storage.duckdb_store")
    store = _store("duckpgq")
    try:
        rows = await store.traverse("a", 3)
        assert [row["entity_id"] for row in rows] == ["b", "c", "d"]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert store._pgq_active is False
    finally:
        await store.close()


async def test_merge_orders_by_depth_name_id() -> None:
    """The min-depth merge applies the pinned order and limit in Python."""
    rows: list[dict[str, Any]] = [
        {"entity_id": "n2", "name": "B", "depth": 2},
        {"entity_id": "n1", "name": "B", "depth": 1},
        {"entity_id": "n1", "name": "B", "depth": 3},
        {"entity_id": "n3", "name": "A", "depth": 2},
        {"entity_id": "n1", "name": "A", "depth": 9},
    ]
    merged = duckpgq.merge_min_depth_rows(rows, 2, exclude="n3")
    assert [row["entity_id"] for row in merged] == ["n1", "n2"]


async def test_property_graph_ddl_is_static() -> None:
    """The DDL is a module constant: no user data can reach it."""
    ddl = duckpgq._PROPERTY_GRAPH_DDL
    assert "entities" in ddl and "relations" in ddl
    assert "{" not in ddl and "%" not in ddl


async def test_unavailable_error_is_typed() -> None:
    """The failure class maps onto the shared taxonomy."""
    assert issubclass(DuckPGQUnavailableError, DatabaseUnavailableError)


async def test_factory_refuses_unknown_engine() -> None:
    """An unknown graph.duckdb_graph_backend is refused, not downgraded."""
    from corpus_kb.storage.graph_factory import create_graph_store

    with pytest.raises(ValueError, match="Unknown DuckDB graph backend"):
        await create_graph_store(
            {"database": {"backend": "duckdb"}, "graph": {"duckdb_graph_backend": "bogus"}},
            source=None,
        )
