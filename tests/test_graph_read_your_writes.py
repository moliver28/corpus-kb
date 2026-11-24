"""Graph writes answer read-your-writes: add-entity lands the read models.

``add_entity``/``add_relation`` used to save events only. The graph reads
kept answering from stale store tables until an unrelated command advanced
the projections (issue #530). The tool functions now fold the documents
projection before the envelope returns. These tests pin that contract on
both engines, plus the envelope key-set the parity catalogue carries.

Hermetic duck legs: one tmp ``.duckdb`` file plus the sqlite event
sidecar, no Ollama. The pg leg skips when Postgres is unreachable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.handlers.graph_handler import GraphHandler
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_store import DuckDbGraphStore
from corpus_kb.tools.graph_tools import add_entity, add_relation

TEST_TENANT = "00000000-0000-0000-0000-000000000001"

# Same resolution the pg_pool fixture uses, so the tool's event store and
# the projection reader name the same database the pool opened.
_PG_TEST_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb_test"


@pytest.fixture(autouse=True)
def _close_event_apps():
    """Drop the cached event app and the research-handler singleton.

    Both are process-wide singletons keyed by DSN; a pg test ahead of a
    duck test would otherwise hand the duck advance the postgres reader.
    """
    from corpus_kb.domain.application import reset_app
    from corpus_kb.handlers.research_handler import reset_research_handler

    yield
    reset_app()
    reset_research_handler()


@pytest.fixture
async def duck_gateway(duckdb_path: Path, monkeypatch: pytest.MonkeyPatch) -> DuckDbGateway:
    """A migrated duck file; the env DSN pins the event sidecar to it."""
    dsn = f"duckdb:///{duckdb_path.as_posix()}"
    run_duckdb_migrations(dsn)
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", dsn)
    gateway = await DuckDbGateway.connect(dsn)
    yield gateway
    await gateway.close()


def _duck_config(duckdb_path: Path) -> dict[str, object]:
    """The tool config: duckdb backend, the same file the gateway opened."""
    return {
        "database": {
            "backend": "duckdb",
            "connection_string": f"duckdb:///{duckdb_path.as_posix()}",
        }
    }


async def test_add_entity_then_search_graph_pg(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entity is searchable right after the tool envelope returns."""
    from corpus_kb.storage.graph_store import PostgresGraphStore

    pinned = os.environ.get("CORPUS_KB_DATABASE_URL", _PG_TEST_DSN)
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", pinned)
    name = f"ryw-pg-{uuid4().hex[:12]}"

    result = await add_entity(pg_pool, name, tenant_id=TEST_TENANT)

    assert result["status"] == "success"
    handler = GraphHandler(PostgresGraphStore(pg_pool))
    found = await handler.handle_search_graph(UUID(TEST_TENANT), name)
    assert [row["name"] for row in found] == [name]


async def test_add_entity_then_search_graph_duck(
    duck_gateway: DuckDbGateway, duckdb_path: Path
) -> None:
    """The entity is searchable right after the tool envelope returns."""
    name = f"ryw-duck-{uuid4().hex[:12]}"

    result = await add_entity(
        duck_gateway, name, config=_duck_config(duckdb_path), tenant_id=TEST_TENANT
    )

    assert result["status"] == "success"
    store = DuckDbGraphStore(duck_gateway.duplicate_connection())
    found = await GraphHandler(store).handle_search_graph(UUID(TEST_TENANT), name)
    assert [row["name"] for row in found] == [name]


async def test_add_relation_then_get_entity_relations_duck(
    duck_gateway: DuckDbGateway, duckdb_path: Path
) -> None:
    """A relation built from tool-returned ids reads back immediately."""
    config = _duck_config(duckdb_path)
    first = await add_entity(
        duck_gateway, f"ryw-a-{uuid4().hex[:8]}", config=config, tenant_id=TEST_TENANT
    )
    second = await add_entity(
        duck_gateway, f"ryw-b-{uuid4().hex[:8]}", config=config, tenant_id=TEST_TENANT
    )

    result = await add_relation(
        duck_gateway,
        str(first["entity_id"]),
        str(second["entity_id"]),
        relation_type="uses",
        config=config,
        tenant_id=TEST_TENANT,
    )

    assert result["status"] == "success"
    store = DuckDbGraphStore(duck_gateway.duplicate_connection())
    handler = GraphHandler(store)
    rows = await handler.handle_get_entity_relations(
        UUID(TEST_TENANT), UUID(str(first["entity_id"]))
    )
    assert len(rows) == 1
    assert rows[0]["relation_type"] == "uses"


async def test_add_entity_envelope_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The envelope keys stay exactly {status, entity_id, name, entity_type}."""
    from corpus_kb.handlers import command_handler as command_handler_module
    from corpus_kb.projections import catchup as catchup_module

    advances: list[tuple[Any, UUID]] = []

    async def fake_advance(pool: Any, tenant_id: UUID) -> None:
        advances.append((pool, tenant_id))

    def fake_handle(self: Any, cmd: Any) -> dict[str, object]:
        return {
            "status": "success",
            "entity_id": "00000000-0000-0000-0000-0000000000aa",
            "name": cmd.name,
            "entity_type": cmd.entity_type,
        }

    monkeypatch.setattr(command_handler_module.CommandHandler, "handle_add_entity", fake_handle)
    monkeypatch.setattr(catchup_module, "advance_graph_projections", fake_advance)

    pool = object()
    result = await add_entity(pool, "parity-entity", tenant_id=TEST_TENANT)

    assert set(result) == {"status", "entity_id", "name", "entity_type"}
    assert advances == [(pool, UUID(TEST_TENANT))]
