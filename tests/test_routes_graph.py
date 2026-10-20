"""Route tests for api/routes_graph.py: tenant context and stable order.

Two v1 accidents are pinned here:

* GET /api/graph/relations/{entity_id} answered only the default tenant.
  The tenant now rides the query string, matching every other GET route.
* Relation rows had no total order, so two identical calls could list the
  same rows in a different order. The store query now sorts by relation_id
  ASC (zero-drift rule 9), and the route passes that order through.

Offline: no Postgres, no Ollama.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID, uuid4

import pytest
from starlette.testclient import TestClient

import corpus_kb.handlers.graph_handler as graph_handler_module
from corpus_kb.api.http import create_http_app
from corpus_kb.storage.graph_store import PostgresGraphStore

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"


# ============================================================================
# Fakes
# ============================================================================


class _RecordingGraphHandler:
    """Stub handler that records calls and returns fixed rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[tuple[UUID, UUID, int | None]] = []

    async def handle_get_entity_relations(
        self, tenant_id: UUID, entity_id: UUID, limit: int | None = None
    ) -> list[dict[str, Any]]:
        self.calls.append((tenant_id, entity_id, limit))
        rows = [dict(row, tenant_id=str(tenant_id)) for row in self._rows]
        return rows if limit is None else rows[:limit]


class _ScriptedConn:
    """asyncpg connection fake: records SQL, returns canned relation rows."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.fetch_sql: str | None = None
        self.fetch_args: tuple[object, ...] = ()

    async def execute(self, sql: str, *args: object) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.fetch_sql = sql
        self.fetch_args = args
        return list(self.rows)


class _OneConnPool:
    """asyncpg pool fake that always yields the one scripted connection."""

    def __init__(self, conn: _ScriptedConn) -> None:
        self._conn = conn
        self.released = 0

    async def acquire(self) -> _ScriptedConn:
        return self._conn

    async def release(self, conn: _ScriptedConn) -> None:
        self.released += 1


def _relation_row(relation_id: str) -> dict[str, Any]:
    return {
        "relation_id": relation_id,
        "source_entity_id": "ent-1",
        "target_entity_id": "ent-2",
        "relation_type": "CALLS",
        "weight": 0.9,
        "metadata": '{"line": 42}',
    }


# ============================================================================
# Tenant parameterization (GET /api/graph/relations/{entity_id})
# ============================================================================


class TestRelationsRouteTenant:
    """The route reads the tenant from the query string."""

    def test_non_default_tenant_reaches_the_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [_relation_row("rel-1")]
        stub = _RecordingGraphHandler(rows)
        monkeypatch.setattr(graph_handler_module, "get_graph_handler", lambda: stub)

        entity_id = uuid4()
        client = TestClient(create_http_app())
        response = client.get(
            f"/api/graph/relations/{entity_id}", params={"tenant_id": OTHER_TENANT}
        )

        assert response.status_code == 200
        assert stub.calls == [(UUID(OTHER_TENANT), entity_id, None)]
        assert response.json()["result"] == [dict(rows[0], tenant_id=OTHER_TENANT)]

    def test_missing_tenant_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stub = _RecordingGraphHandler([])
        monkeypatch.setattr(graph_handler_module, "get_graph_handler", lambda: stub)

        entity_id = uuid4()
        client = TestClient(create_http_app())
        response = client.get(f"/api/graph/relations/{entity_id}")

        assert response.status_code == 200
        assert stub.calls == [(UUID(DEFAULT_TENANT), entity_id, None)]


# ============================================================================
# Stable ordering (zero-drift rule 9)
# ============================================================================


class TestRelationsRouteOrdering:
    """Two identical calls return the same rows in the same order."""

    def test_two_identical_calls_return_identical_envelopes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Scripted in NON-sorted order: pass-through must keep it stable,
        # not silently re-sort at the route layer.
        rows = [_relation_row("rel-c"), _relation_row("rel-a"), _relation_row("rel-b")]
        stub = _RecordingGraphHandler(rows)
        monkeypatch.setattr(graph_handler_module, "get_graph_handler", lambda: stub)

        entity_id = uuid4()
        client = TestClient(create_http_app())
        first = client.get(f"/api/graph/relations/{entity_id}")
        second = client.get(f"/api/graph/relations/{entity_id}")

        assert first.json() == second.json()
        listed = [row["relation_id"] for row in first.json()["result"]]
        assert listed == ["rel-c", "rel-a", "rel-b"]


# ============================================================================
# Postgres store: the relation listing query carries the total order
# ============================================================================


class TestPostgresStoreRelationsOrdering:
    """PostgresGraphStore.get_entity_relations sorts by relation_id ASC."""

    @pytest.mark.asyncio
    async def test_query_orders_by_relation_id_and_rows_pass_through(self) -> None:
        conn = _ScriptedConn([_relation_row("rel-2"), _relation_row("rel-1")])
        store = PostgresGraphStore(_OneConnPool(conn), tenant_id=OTHER_TENANT)

        relations = await store.get_entity_relations("ent-1")

        assert conn.fetch_sql is not None
        assert re.search(r"ORDER BY\s+relation_id\s+ASC", conn.fetch_sql) is not None
        # The store tenant is the first SQL argument (RLS filter).
        assert conn.fetch_args[:2] == (OTHER_TENANT, "ent-1")
        assert [rel.relation_id for rel in relations] == ["rel-2", "rel-1"]
        assert relations[0].source_entity_id == "ent-1"
        assert relations[0].target_entity_id == "ent-2"
        assert relations[0].relation_type == "CALLS"
        assert relations[0].metadata == {"line": 42}
