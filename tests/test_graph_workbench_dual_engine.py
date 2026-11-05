"""The Phase-2 Wave 4 graph C+R ops on BOTH engines (D2 parity).

One flow — create two entities, project, find (list window + name
search), link them, inspect (relations, neighbors, traverse) — runs
through the SAME shared tool functions every surface routes through,
against the DuckDB file gateway AND the Postgres pool. Real gateways,
no stubs: the writes ride the event store + DocumentsProjection
catch-up exactly as the server runs them, and the reads ride the
engine's graph store (``duckdb_store``/duckpgq mirror path).

Postgres skips cleanly when the database is not reachable (the conftest
pg_pool contract), so CI's no-Postgres legs still prove the DuckDB arm.
Entity names and types carry a run-unique suffix: the Postgres test
database keeps its rows between runs, so every run writes fresh rows
and reads back only its own (deterministic order, rule 9).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.handlers.command_handler import CommandHandler
from corpus_kb.projections.checkpoint import CheckpointManager
from corpus_kb.projections.dlq import DLQHandler
from corpus_kb.projections.documents_projection import DocumentsProjection
from corpus_kb.projections.event_reader import EventReader
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")

_DUCKDB_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test"


async def _catch_up(handler: CommandHandler, projection: DocumentsProjection, tenant: UUID) -> None:
    """Materialize the aggregate events into the engine's graph tables."""
    app = handler.app
    reader = EventReader(None, app.mapper, app.recorder.events_table_name, recorder=app.recorder)
    await projection.catch_up(reader, tenant)


@pytest.fixture
async def duckdb_env(tmp_path: Path):
    from corpus_kb.storage.gateway import DuckDbGateway

    db = tmp_path / "graph.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(db)))
    gateway = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(db)))
    try:
        # DuckDB splits the stores: the read tools take the analytics
        # file, the write tools take the event store (the SQLite recorder
        # file — never the analytics file, which SQLite cannot open). The
        # future DuckDB server wiring composes the same pair.
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
def postgres_env(pg_pool, monkeypatch: pytest.MonkeyPatch):  # pg_pool skips when Postgres is down
    # get_app() resolves the Postgres DSN from the environment (never from
    # the passed config), so pin it to the SAME database pg_pool opened.
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", _DUCKDB_DSN)
    cfg = {"database": {"backend": "postgres", "connection_string": _DUCKDB_DSN}}
    handler = CommandHandler(cfg, pool=pg_pool)
    projection = DocumentsProjection(pg_pool, CheckpointManager(pg_pool), DLQHandler(pg_pool))
    # Postgres keeps events and analytics in one database: one config.
    return {
        "pool": pg_pool,
        "read_cfg": cfg,
        "write_cfg": cfg,
        "handler": handler,
        "projection": projection,
    }


@pytest.fixture(params=["duckdb", "postgres"])
def env(request: pytest.FixtureRequest) -> dict[str, Any]:
    return request.getfixturevalue(f"{request.param}_env")


@pytest.fixture(autouse=True)
def _reset_singletons():
    from corpus_kb.domain.application import reset_app
    from corpus_kb.handlers.graph_handler import reset_graph_handler

    reset_graph_handler()
    reset_app()
    yield
    reset_graph_handler()
    reset_app()


async def test_create_find_inspect_link_on_both_engines(env: dict[str, Any]) -> None:
    from corpus_kb.tools.entity_read_tools import list_entities
    from corpus_kb.tools.graph_read_tools import (
        get_entity_relations,
        graph_neighbors,
        graph_traverse,
        search_graph,
    )
    from corpus_kb.tools.graph_tools import add_entity, add_relation

    run = uuid4().hex[:8]
    etype = f"wt-{run}"
    pool = env["pool"]
    read_cfg = env["read_cfg"]
    write_cfg = env["write_cfg"]
    handler = cast("CommandHandler", env["handler"])
    projection = cast("DocumentsProjection", env["projection"])

    # CREATE: the two entity writes through the shared tool (the one call
    # POST /api/entities and the socket method make).
    alpha = await add_entity(pool, f"Alpha-{run}", config=write_cfg, entity_type=etype)
    beta = await add_entity(pool, f"Beta-{run}", config=write_cfg, entity_type=etype)
    assert alpha["status"] == "success" and beta["status"] == "success"
    assert set(alpha) == {"status", "entity_id", "name", "entity_type"}
    await _catch_up(handler, projection, TENANT)

    # FIND: the name-ordered list window (limit+offset), the type filter,
    # and the name search — one read each, rows only from this run.
    listed = await list_entities(pool, config=read_cfg, entity_type=etype, limit=1, offset=0)
    assert listed == {
        "status": "success",
        "result": [
            {
                "entity_id": alpha["entity_id"],
                "name": f"Alpha-{run}",
                "entity_type": etype,
                "metadata": {},
            }
        ],
    }
    page2 = await list_entities(pool, config=read_cfg, entity_type=etype, limit=1, offset=1)
    assert [row["entity_id"] for row in page2["result"]] == [beta["entity_id"]]
    found = await search_graph(pool, f"Alpha-{run}", config=read_cfg, entity_type=etype)
    assert found["status"] == "success"
    assert [row["name"] for row in found["result"]] == [f"Alpha-{run}"]

    # LINK: the relation write through the shared tool.
    linked = await add_relation(
        pool,
        alpha["entity_id"],
        beta["entity_id"],
        config=write_cfg,
        relation_type="links",
    )
    assert linked["status"] == "success"
    assert set(linked) == {"status", "relation_id", "relation_type"}
    await _catch_up(handler, projection, TENANT)

    # INSPECT: the relations read, the 1-hop neighbors, and the walk.
    relations = await get_entity_relations(pool, alpha["entity_id"], config=read_cfg)
    assert relations["status"] == "success"
    assert len(relations["result"]) == 1
    row = relations["result"][0]
    assert row["relation_type"] == "links"
    names = {row.get("source_name"), row.get("target_name")}
    assert names == {f"Alpha-{run}", f"Beta-{run}"}

    neighbors = await graph_neighbors(pool, alpha["entity_id"], config=read_cfg, hops=1)
    assert neighbors["status"] == "success"
    assert any(r["name"] == f"Beta-{run}" for r in neighbors["result"])

    walked = await graph_traverse(pool, alpha["entity_id"], config=read_cfg, max_depth=2)
    assert walked["status"] == "success"
    assert any(r["name"] == f"Beta-{run}" for r in walked["result"])
