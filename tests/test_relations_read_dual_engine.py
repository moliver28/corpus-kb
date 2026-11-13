"""The Phase-2 Wave 6 relations-list read on BOTH engines (D2 parity).

One flow — seed three entities with two relations, read the window
through the SAME shared tool function every surface routes through,
page it, and check the total order — runs against the DuckDB file
gateway AND the Postgres pool. The seed rides ``tenant_connection`` (the
same scope the reads use), so the Postgres leg sits inside the RLS
tenant config and the DuckDB leg inside the gateway's transaction
scope. Postgres skips cleanly when the database is not reachable (the
conftest pg_pool contract), so CI's no-Postgres legs still prove the
DuckDB arm.

Entity and relation rows carry a run-unique name suffix: the Postgres
test database keeps its rows between runs, so every run seeds fresh
rows and reads back only its own (deterministic order, rule 9).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string
from corpus_kb.storage.gateway import is_gateway
from corpus_kb.storage.tenant_conn import tenant_connection

TENANT = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
async def duckdb_pool(tmp_path):
    from corpus_kb.storage.gateway import DuckDbGateway

    path = tmp_path / "relations.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(path)))
    gateway = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(path)))
    try:
        yield gateway
    finally:
        await gateway.close()


@pytest.fixture
async def postgres_pool(pg_pool):  # pg_pool skips when Postgres is down
    yield pg_pool


@pytest.fixture(params=["duckdb", "postgres"])
def pool(request: pytest.FixtureRequest):
    return request.getfixturevalue(f"{request.param}_pool")


async def _seed_graph(pool, run: str) -> list[UUID]:
    """Three entities, two relations; return the two relation ids."""
    if is_gateway(pool):
        entity_sql = (
            "INSERT INTO entities (entity_id, tenant_id, name, entity_type)"
            " VALUES (?, ?, ?, ?)"
        )
        relation_sql = (
            "INSERT INTO relations (relation_id, tenant_id, source_entity_id,"
            " target_entity_id, relation_type, weight) VALUES (?, ?, ?, ?, ?, ?)"
        )
    else:
        entity_sql = (
            "INSERT INTO entities (entity_id, tenant_id, name, entity_type)"
            " VALUES ($1, $2, $3, $4)"
        )
        relation_sql = (
            "INSERT INTO relations (relation_id, tenant_id, source_entity_id,"
            " target_entity_id, relation_type, weight) VALUES ($1, $2, $3, $4, $5, $6)"
        )
    ids = [uuid4() for _ in range(3)]
    names = [f"aaa-{run}-a", f"aaa-{run}-b", f"aaa-{run}-c"]
    async with tenant_connection(pool, TENANT) as conn:
        for entity_id, name in zip(ids, names, strict=True):
            await conn.execute(entity_sql, str(entity_id), str(TENANT), name, "concept")
        # Two relations with DIFFERENT types. The types carry the run
        # prefix and sort before every real type ("0" sorts first), so
        # this run's rows sit at the front of the shared test database's
        # window — the LIMIT cut can never hide one of them.
        first = uuid4()
        second = uuid4()
        await conn.execute(
            relation_sql, str(first), str(TENANT), str(ids[0]), str(ids[1]), f"00-rel-{run}", 1.0
        )
        await conn.execute(
            relation_sql, str(second), str(TENANT), str(ids[1]), str(ids[2]), f"00-sup-{run}", 2.5
        )
    return [first, second]


def _rows_for_run(result: list[dict], run: str) -> list[dict]:
    """This run's rows: the seeded names carry the run suffix."""
    return [row for row in result if run in f"{row['source_name']}{row['target_name']}"]


async def test_relations_list_window_order_on_both_engines(pool) -> None:
    from corpus_kb.tools.relation_read_tools import relations_list

    run = uuid4().hex[:8]
    first, second = await _seed_graph(pool, run)

    listed = await relations_list(pool, limit=100, offset=0)
    assert listed["status"] == "success"
    rows = _rows_for_run(listed["result"], run)
    assert [row["relation_id"] for row in rows] == [str(first), str(second)]
    assert [row["relation_type"] for row in rows] == [f"00-rel-{run}", f"00-sup-{run}"]
    assert rows[1]["weight"] == 2.5
    assert rows[1]["source_name"] == f"aaa-{run}-b"
    assert rows[1]["target_name"] == f"aaa-{run}-c"

    # The window pages honestly: the run's two rows lead the tenant's
    # order, so limit 2 answers exactly them, and offset 2 answers none.
    page = await relations_list(pool, limit=2, offset=0)
    assert [row["relation_id"] for row in page["result"]] == [str(first), str(second)]
    empty = await relations_list(pool, limit=2, offset=2)
    assert _rows_for_run(empty["result"], run) == []

    # The shared-count guard answers the one typed error on this arm too
    # (the parity test proves the other surfaces answer the same way).
    from corpus_kb.errors import ValidationError

    with pytest.raises(ValidationError):
        await relations_list(pool, limit=0, offset=0)
