"""Wave-8 graph U/D store-level parity on BOTH engines (D2(a)).

One arm set — update entity, delete entity (cascade), update relation,
delete relation — runs against the Postgres pool AND the DuckDB file
gateway through the SAME GraphStore methods the handler calls. Real
stores, no stubs. Postgres skips cleanly when the database is not
reachable (the conftest pg_pool contract). Names carry a run-unique
suffix: the Postgres test database keeps its rows between runs, so every
run writes fresh rows and reads back only its own (deterministic, rule 9).

The AGE arms ride the same in-memory fake pool as
``tests/test_age_graph_store.py``: they pin the generated cypher text
(param-map-only values, no interpolation) and the application-level
pre-checks without an AGE server.

Deletes are HARD deletes by decision (no tombstones, no audit): the
arms assert exact removal counts and idempotence instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from test_age_graph_store import FakeConnection, FakePool, cypher_calls

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.errors import ConflictError
from corpus_kb.storage.age_graph_store import AgeGraphStore
from corpus_kb.storage.duckdb_store import DuckDbGraphStore
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.graph_store import DEFAULT_TENANT_ID, GraphStore, PostgresGraphStore
from corpus_kb.utils.models import Chunk, Document, Entity, Relation


def _entity(name: str, entity_type: str, **kw: Any) -> Entity:
    return Entity(name=name, entity_type=entity_type, source_type="code", **kw)


@pytest.fixture
async def duckdb_env(tmp_path: Path):
    db = tmp_path / "writes.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(db)))
    from corpus_kb.storage.gateway import DuckDbGateway

    gateway = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(db)))
    try:
        yield {"store": DuckDbGraphStore(gateway.duplicate_connection()), "raw": gateway}
    finally:
        await gateway.close()


@pytest.fixture
def postgres_env(pg_pool):  # pg_pool skips when Postgres is down
    return {"store": PostgresGraphStore(pg_pool), "raw": pg_pool}


@pytest.fixture(params=["duckdb", "postgres"])
def env(request: pytest.FixtureRequest) -> dict[str, Any]:
    return request.getfixturevalue(f"{request.param}_env")


class _Scoped:
    """Raw SQL helper carrying the tenant GUC on Postgres (RLS posture).

    DuckDB has no RLS: the gateway passes through. On Postgres every
    statement runs on a fresh connection with ``app.current_tenant_id``
    set first — the same thing the store's ``_get_conn`` does.
    """

    def __init__(self, raw: Any, tenant_id: str) -> None:
        self._raw = raw
        self._tenant = tenant_id

    async def _conn(self) -> Any:
        import asyncpg

        if isinstance(self._raw, asyncpg.Pool):
            conn = await self._raw.acquire()
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, false)", self._tenant
            )
            return conn
        return self._raw

    async def _release(self, conn: Any) -> None:
        import asyncpg

        if isinstance(conn, asyncpg.Connection):
            await self._raw.release(conn)

    async def execute(self, sql: str, *args: object) -> Any:
        conn = await self._conn()
        try:
            return await conn.execute(sql, *args)
        finally:
            await self._release(conn)

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        conn = await self._conn()
        try:
            return [dict(row) for row in await conn.fetch(sql, *args)]
        finally:
            await self._release(conn)


# ============================================================================
# ABC enforcement
# ============================================================================


def test_abc_declares_the_four_new_methods() -> None:
    for name in ("update_entity", "delete_entity", "update_relation", "delete_relation"):
        assert name in GraphStore.__abstractmethods__


class _PreWaveStore(GraphStore):
    """Implements every pre-Wave-8 abstract method; none of the new ones."""

    async def add_entity(self, entity: Entity) -> str:
        return ""

    async def add_relation(self, relation: Relation) -> str:
        return ""

    async def get_entity(self, entity_id: str) -> Entity | None:
        return None

    async def search_entities(self, name: str, entity_type: str | None = None) -> list[Entity]:
        return []

    async def get_entity_relations(self, entity_id: str) -> list[Relation]:
        return []

    async def bfs(self, start_entity_id: str, max_depth: int = 5) -> dict[str, Any]:
        return {}

    async def neighbors(
        self,
        entity_id: str,
        hops: int = 1,
        direction: str = "both",
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return []

    async def traverse(
        self,
        entity_id: str,
        max_depth: int = 5,
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return []

    async def close(self) -> None:
        return None

    def transaction(self):
        raise NotImplementedError


def test_store_missing_a_new_method_fails_to_instantiate() -> None:
    with pytest.raises(TypeError) as excinfo:
        _PreWaveStore()
    assert "update_entity" in str(excinfo.value)


# ============================================================================
# Both-engine arms
# ============================================================================


async def test_update_entity_reflects_on_reads(env: dict[str, Any]) -> None:
    store = env["store"]
    run = uuid4().hex[:8]
    added = await store.add_entity(_entity(f"Old-{run}", f"wt-{run}"))
    saved = await store.update_entity(
        _entity(f"New-{run}", f"nt-{run}", entity_id=added, metadata={"k": "v"})
    )
    assert saved is not None
    assert saved.entity_id == added
    assert saved.name == f"New-{run}"
    assert saved.entity_type == f"nt-{run}"
    assert saved.metadata == {"k": "v"}
    fetched = await store.get_entity(added)
    assert fetched is not None and fetched.name == f"New-{run}"
    found = await store.search_entities(f"New-{run}")
    assert any(row.entity_id == added for row in found)
    assert await store.update_entity(_entity("Ghost", "x", entity_id=str(uuid4()))) is None


async def test_update_linked_entity_preserves_relations(env: dict[str, Any]) -> None:
    store = env["store"]
    run = uuid4().hex[:8]
    alpha = await store.add_entity(_entity(f"A-{run}", f"wt-{run}"))
    beta = await store.add_entity(_entity(f"B-{run}", f"wt-{run}"))
    linked = await store.add_relation(
        Relation(
            source_entity_id=alpha,
            target_entity_id=beta,
            relation_type="links",
            weight=2.5,
        )
    )
    renamed = await store.update_entity(_entity(f"A2-{run}", f"wt-{run}", entity_id=alpha))
    assert renamed is not None
    relations = await store.get_entity_relations(alpha)
    assert [r.relation_id for r in relations] == [linked]
    assert [r.weight for r in relations] == [2.5]
    assert await store.get_entity(beta) is not None


async def test_rename_collision_conflicts(env: dict[str, Any]) -> None:
    store = env["store"]
    run = uuid4().hex[:8]
    first = await store.add_entity(_entity(f"Taken-{run}", f"wt-{run}"))
    second = await store.add_entity(_entity(f"Free-{run}", f"wt-{run}"))
    with pytest.raises(ConflictError):
        await store.update_entity(_entity(f"Taken-{run}", f"wt-{run}", entity_id=second))
    kept = await store.get_entity(first)
    assert kept is not None and kept.name == f"Taken-{run}"


async def test_delete_entity_cascades_and_is_idempotent(env: dict[str, Any]) -> None:
    store = env["store"]
    run = uuid4().hex[:8]
    hub = await store.add_entity(_entity(f"Hub-{run}", f"wt-{run}"))
    left = await store.add_entity(_entity(f"L-{run}", f"wt-{run}"))
    right = await store.add_entity(_entity(f"R-{run}", f"wt-{run}"))
    await store.add_relation(
        Relation(source_entity_id=hub, target_entity_id=left, relation_type="l")
    )
    await store.add_relation(
        Relation(source_entity_id=right, target_entity_id=hub, relation_type="r")
    )
    removed = await store.delete_entity(hub)
    assert removed == 2
    assert await store.get_entity(hub) is None
    assert await store.get_entity_relations(hub) == []
    near = await store.neighbors(left, hops=1)
    assert all(row["entity_id"] != hub for row in near)
    assert await store.delete_entity(hub) is None


async def test_delete_relation_removes_exactly_one_row(env: dict[str, Any]) -> None:
    store = env["store"]
    run = uuid4().hex[:8]
    alpha = await store.add_entity(_entity(f"A-{run}", f"wt-{run}"))
    beta = await store.add_entity(_entity(f"B-{run}", f"wt-{run}"))
    linked = await store.add_relation(
        Relation(source_entity_id=alpha, target_entity_id=beta, relation_type="links")
    )
    assert await store.delete_relation(linked) is True
    assert await store.get_entity_relations(alpha) == []
    assert await store.get_entity(beta) is not None
    assert await store.delete_relation(linked) is False


async def test_update_relation_keeps_provenance_and_endpoints(env: dict[str, Any]) -> None:
    store, raw = env["store"], env["raw"]
    raw_sql = _Scoped(raw, DEFAULT_TENANT_ID)
    run = uuid4().hex[:8]
    doc_id = await store.add_document(
        Document(path=f"p-{run}.txt", source_type="text", content="c", size_bytes=1)
    )
    chunk_id = await store.add_chunk(
        Chunk(document_id=doc_id, text=f"text-{run}", source_type="text")
    )
    alpha = await store.add_entity(_entity(f"A-{run}", f"wt-{run}"))
    beta = await store.add_entity(_entity(f"B-{run}", f"wt-{run}"))
    relation_id = str(uuid4())
    await raw_sql.execute(
        """
        INSERT INTO relations (relation_id, tenant_id, source_entity_id,
            target_entity_id, relation_type, weight, metadata, chunk_id,
            confidence, extractor_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        relation_id,
        DEFAULT_TENANT_ID,
        alpha,
        beta,
        "links",
        1.0,
        "{}",
        chunk_id,
        0.42,
        "seed-extractor",
    )
    saved = await store.update_relation(
        Relation(
            relation_id=relation_id,
            source_entity_id=alpha,
            target_entity_id=beta,
            relation_type="recategorized",
            weight=3.5,
            metadata={"note": "edited"},
        )
    )
    assert saved is not None
    assert saved.relation_type == "recategorized"
    rows = await raw_sql.fetch(
        """
        SELECT relation_type, weight, chunk_id, confidence, extractor_id,
               source_entity_id, target_entity_id
        FROM relations WHERE relation_id = $1
        """,
        relation_id,
    )
    row = rows[0]
    assert row["relation_type"] == "recategorized"
    assert float(row["weight"]) == 3.5
    assert str(row["chunk_id"]) == chunk_id
    assert float(row["confidence"]) == 0.42
    assert str(row["extractor_id"]) == "seed-extractor"
    # Endpoints are immutable: a mutated model changes nothing in the row.
    await store.update_relation(
        Relation(
            relation_id=relation_id,
            source_entity_id=str(uuid4()),
            target_entity_id=beta,
            relation_type="recategorized",
            weight=3.5,
        )
    )
    after = (
        await raw_sql.fetch(
            "SELECT source_entity_id, target_entity_id FROM relations WHERE relation_id = $1",
            relation_id,
        )
    )[0]
    assert str(after["source_entity_id"]) == alpha
    assert str(after["target_entity_id"]) == beta
    assert (
        await store.update_relation(
            Relation(
                relation_id=str(uuid4()),
                source_entity_id=alpha,
                target_entity_id=beta,
                relation_type="links",
                weight=1.0,
            )
        )
        is None
    )


# ============================================================================
# AGE arms (fake pool; pins cypher text + pre-checks without a server)
# ============================================================================


def test_age_write_cypher_pin_no_interpolation() -> None:
    conn = FakeConnection()
    store = AgeGraphStore(FakePool(conn))
    nasty = "Nasty'); DETACH DELETE e //"

    async def _drive() -> None:
        await store.update_entity(_entity(nasty, f"wt-{nasty[:6]}", entity_id="e1"))
        await store.delete_entity("e1")
        await store.update_relation(
            Relation(
                relation_id="r1",
                source_entity_id="s1",
                target_entity_id="t1",
                relation_type=nasty,
                weight=2.0,
            )
        )
        await store.delete_relation("r1")

    import asyncio

    asyncio.run(_drive())
    calls = cypher_calls(conn)
    assert len(calls) == 7
    texts = [sql for sql, _args in calls]
    joined = "\n".join(texts)
    assert "SET e.name = $name" in joined
    assert "SET r.relation_type = $relation_type" in joined
    assert "DETACH DELETE e" in joined
    assert "DELETE r\nRETURN r.relation_id" in joined
    assert "count(r)" in joined
    for sql, args in calls:
        assert nasty not in sql
        assert "cypher('corpus_kb'" in sql
        assert len(args) == 1 and isinstance(args[0], str)
        json.loads(args[0])
    # The rename's values ride the param map of the first two calls only.
    assert nasty in calls[0][1][0]
    assert nasty in calls[1][1][0]


def test_age_duplicate_precheck_conflicts() -> None:
    conn = FakeConnection()
    conn.add_fetch_response("o.entity_id <> $entity_id", [{"entity_id": "other"}])
    store = AgeGraphStore(FakePool(conn))

    import asyncio

    async def _drive() -> None:
        await store.update_entity(_entity("Taken", "wt", entity_id="e1"))

    with pytest.raises(ConflictError):
        asyncio.run(_drive())


def test_age_detach_cascade_reports_edge_count() -> None:
    conn = FakeConnection()
    conn.add_fetch_response("count(r)", [{"n": 3}])
    conn.add_fetch_response("DETACH DELETE", [{"entity_id": "e1"}])
    store = AgeGraphStore(FakePool(conn))

    import asyncio

    assert asyncio.run(store.delete_entity("e1")) == 3


def test_age_unknown_ids_answer_honestly() -> None:
    conn = FakeConnection()
    store = AgeGraphStore(FakePool(conn))

    import asyncio

    async def _drive() -> tuple[Any, Any, Any, bool]:
        return (
            await store.update_entity(_entity("X", "wt", entity_id="missing")),
            await store.delete_entity("missing"),
            await store.update_relation(
                Relation(
                    relation_id="missing",
                    source_entity_id="s",
                    target_entity_id="t",
                    relation_type="links",
                    weight=1.0,
                )
            ),
            await store.delete_relation("missing"),
        )

    entity_result, delete_result, relation_result, gone = asyncio.run(_drive())
    assert entity_result is None
    assert delete_result is None
    assert relation_result is None
    assert gone is False


# ============================================================================
# RYW through the tool leg (no projection catch-up on the U/D paths)
# ============================================================================


@pytest.fixture
async def duckdb_ryw_env(tmp_path: Path):
    from corpus_kb.handlers.command_handler import CommandHandler
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.documents_projection import DocumentsProjection
    from corpus_kb.storage.gateway import DuckDbGateway

    db = tmp_path / "ryw.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(db)))
    gateway = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(db)))
    try:
        read_cfg = {
            "database": {
                "backend": "duckdb",
                "connection_string": duckdb_path_to_conn_string(str(db)),
            }
        }
        write_cfg = {
            "database": {"backend": "duckdb", "connection_string": str(tmp_path / "events.store")}
        }
        handler = CommandHandler(write_cfg, pool=gateway)
        projection = DocumentsProjection(
            None,
            CheckpointManager(gateway=gateway),
            DLQHandler(gateway=gateway),
            gateway=gateway,
        )
        yield {
            "pool": gateway,
            "read_cfg": read_cfg,
            "write_cfg": write_cfg,
            "handler": handler,
            "projection": projection,
        }
    finally:
        await gateway.close()


@pytest.fixture
def postgres_ryw_env(pg_pool, monkeypatch: pytest.MonkeyPatch):
    dsn = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test"
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", dsn)
    from corpus_kb.handlers.command_handler import CommandHandler
    from corpus_kb.projections.checkpoint import CheckpointManager
    from corpus_kb.projections.dlq import DLQHandler
    from corpus_kb.projections.documents_projection import DocumentsProjection

    cfg = {"database": {"backend": "postgres", "connection_string": dsn}}
    handler = CommandHandler(cfg, pool=pg_pool)
    projection = DocumentsProjection(pg_pool, CheckpointManager(pg_pool), DLQHandler(pg_pool))
    return {
        "pool": pg_pool,
        "read_cfg": cfg,
        "write_cfg": cfg,
        "handler": handler,
        "projection": projection,
    }


@pytest.fixture(params=["duckdb_ryw_env", "postgres_ryw_env"])
def ryw_env(request: pytest.FixtureRequest) -> dict[str, Any]:
    return request.getfixturevalue(request.param)


@pytest.fixture(autouse=True)
def _reset_write_singletons():
    from corpus_kb.domain.application import reset_app
    from corpus_kb.handlers.graph_handler import reset_graph_handler

    reset_graph_handler()
    reset_app()
    yield
    reset_graph_handler()
    reset_app()


async def test_ryw_all_four_ops_without_catch_up(ryw_env: dict[str, Any]) -> None:
    """Every Wave-8 op answers after the store commit: the very next reads
    see the new state with NO projection catch-up between op and read,
    on both engines (the judged synchronous-store-write RYW divergence).
    """
    from corpus_kb.tools.entity_read_tools import list_entities
    from corpus_kb.tools.graph_read_tools import get_entity_relations
    from corpus_kb.tools.graph_tools import (
        add_entity,
        add_relation,
        delete_entity,
        delete_relation,
        update_entity,
        update_relation,
    )

    run = uuid4().hex[:8]
    etype = f"ryw-{run}"
    pool = ryw_env["pool"]
    read_cfg = ryw_env["read_cfg"]
    write_cfg = ryw_env["write_cfg"]

    alpha = await add_entity(pool, f"Ryw-{run}", config=write_cfg, entity_type=etype)
    beta = await add_entity(pool, f"Ryw2-{run}", config=write_cfg, entity_type=etype)
    linked = await add_relation(
        pool, alpha["entity_id"], beta["entity_id"], config=write_cfg, relation_type="links"
    )

    # UPDATE ENTITY: no catch-up call anywhere.
    saved = await update_entity(
        pool,
        alpha["entity_id"],
        config=read_cfg,
        name=f"Ryw-renamed-{run}",
        entity_type=etype,
    )
    assert saved["status"] == "success"
    listed = await list_entities(pool, config=read_cfg, entity_type=etype, limit=10, offset=0)
    assert f"Ryw-renamed-{run}" in [row["name"] for row in listed["result"]]

    # UPDATE RELATION.
    updated = await update_relation(
        pool, linked["relation_id"], config=read_cfg, relation_type="recategorized", weight=3.5
    )
    assert updated["status"] == "success"
    relations = await get_entity_relations(pool, alpha["entity_id"], config=read_cfg)
    assert relations["result"][0]["relation_type"] == "recategorized"

    # DELETE RELATION.
    gone = await delete_relation(pool, linked["relation_id"], config=read_cfg)
    assert gone == {"status": "success", "relation_id": linked["relation_id"], "deleted": True}
    relations = await get_entity_relations(pool, alpha["entity_id"], config=read_cfg)
    assert relations["result"] == []

    # DELETE ENTITY: the cascade count arrives in the same answer.
    removed = await delete_entity(pool, beta["entity_id"], config=read_cfg)
    assert removed["status"] == "success"
    listed = await list_entities(pool, config=read_cfg, entity_type=etype, limit=10, offset=0)
    assert [row["name"] for row in listed["result"]] == [f"Ryw-renamed-{run}"]
