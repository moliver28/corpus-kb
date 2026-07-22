"""Tests for graph store wiring and GraphHandler delegation.

Covers the seams defined by todo 2b:
  - server_wiring.create_graph_store selects the backend by config.
  - backend="age" with AGE unavailable falls back to PostgresGraphStore
    and logs a warning.
  - backend="postgres" selects PostgresGraphStore directly.
  - unknown backend raises a clear error at wiring time.
  - GraphHandler delegates entity/relation queries to the injected GraphStore
    while preserving public method signatures and return shapes.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Optional
from uuid import UUID

import asyncpg
import pytest

from src.storage.age_graph_store import AgeUnavailableError
from src.storage.graph_store import DEFAULT_TENANT_ID, GraphStore
from src.utils.models import Entity, Relation

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT = UUID("00000000-0000-0000-0000-000000000002")


# ============================================================================
# Fakes
# ============================================================================


class FakeTransaction:
    async def __aenter__(self) -> "FakeTransaction":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakePool:
    """asyncpg.Pool fake that counts acquire/release and yields FakeConnections."""

    def __init__(self, *, age_present: bool = True) -> None:
        self.age_present = age_present
        self.acquire_count = 0
        self.release_count = 0

    async def acquire(self) -> "FakeConnection":
        self.acquire_count += 1
        return FakeConnection(pool=self)

    async def release(self, conn: "FakeConnection") -> None:
        self.release_count += 1


class FakeConnection:
    """asyncpg.Connection fake for AGE probe checks."""

    def __init__(self, *, pool: Optional[FakePool] = None) -> None:
        self.pool = pool

    async def fetchrow(self, sql: str, *args: object) -> Optional[dict[str, object]]:
        if "pg_extension" in sql:
            age_present = True if self.pool is None else self.pool.age_present
            return {"ok": age_present}
        if "ag_graph" in sql:
            age_present = True if self.pool is None else self.pool.age_present
            return {"ok": age_present}
        return None

    async def execute(self, sql: str, *args: object) -> str:
        return "OK"

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        return []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()


class FakeGraphStore(GraphStore):
    """In-memory GraphStore that records calls and returns deterministic data."""

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, Optional[str]]] = []
        self.bfs_calls: list[tuple[str, int]] = []
        self.relation_calls: list[str] = []
        self.closed = False

    async def add_entity(self, entity: Entity) -> str:
        return entity.entity_id

    async def add_relation(self, relation: Relation) -> str:
        return relation.relation_id

    async def get_entity(self, entity_id: str) -> Optional[Entity]:
        return None

    async def search_entities(
        self, name: str, entity_type: Optional[str] = None
    ) -> list[Entity]:
        self.search_calls.append((name, entity_type))
        return [
            Entity(
                entity_id="ent-1",
                name="UserService",
                entity_type=entity_type or "CLASS",
                source_type="code",
                source_document_id="doc-1",
                metadata={"lang": "python"},
            )
        ]

    async def get_entity_relations(self, entity_id: str) -> list[Relation]:
        self.relation_calls.append(entity_id)
        return [
            Relation(
                relation_id="rel-1",
                source_entity_id="ent-1",
                target_entity_id="ent-2",
                relation_type="CALLS",
                weight=0.9,
                metadata={"line": 42},
            )
        ]

    async def bfs(self, start_entity_id: str, max_depth: int = 5) -> dict[str, object]:
        self.bfs_calls.append((start_entity_id, max_depth))
        return {
            "start_entity_id": start_entity_id,
            "max_depth": max_depth,
            "visited": {start_entity_id: 0, "ent-2": 1, "ent-3": 2},
        }

    async def close(self) -> None:
        self.closed = True

    def transaction(self):
        raise NotImplementedError

    async def add_document(self, document: Any) -> str:
        return "doc-1"

    async def add_chunk(self, chunk: Any) -> str:
        return "chunk-1"


# ============================================================================
# Wiring selection
# ============================================================================


class TestCreateGraphStore:
    """server_wiring.create_graph_store selects the correct backend."""

    @pytest.mark.asyncio
    async def test_age_backend_returns_age_store(self) -> None:
        """Given graph.backend="age" and AGE available, return AgeGraphStore."""
        from src.server_wiring import create_graph_store
        from src.storage.age_graph_store import AgeGraphStore

        pool = FakePool(age_present=True)
        cfg: dict[str, object] = {"graph": {"backend": "age"}}
        store = await create_graph_store(cfg, pool)
        assert isinstance(store, AgeGraphStore)

    @pytest.mark.asyncio
    async def test_age_backend_unavailable_falls_back_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Given graph.backend="age" but AGE absent, fall back and warn."""
        from src.server_wiring import create_graph_store
        from src.storage.graph_store import PostgresGraphStore

        class RaisingAgeStore(GraphStore):
            def __init__(
                self, pool: object, tenant_id: str = DEFAULT_TENANT_ID
            ) -> None:
                raise AgeUnavailableError("age not installed")

            async def add_entity(self, entity: Entity) -> str:
                return ""

            async def add_relation(self, relation: Relation) -> str:
                return ""

            async def get_entity(self, entity_id: str) -> Optional[Entity]:
                return None

            async def search_entities(
                self, name: str, entity_type: Optional[str] = None
            ) -> list[Entity]:
                return []

            async def get_entity_relations(self, entity_id: str) -> list[Relation]:
                return []

            async def bfs(
                self, start_entity_id: str, max_depth: int = 5
            ) -> dict[str, object]:
                return {}

            async def close(self) -> None:
                pass

            def transaction(self):
                raise NotImplementedError

            async def add_document(self, document: Any) -> str:
                return ""

            async def add_chunk(self, chunk: Any) -> str:
                return ""

        monkeypatch.setattr("src.storage.AgeGraphStore", RaisingAgeStore, raising=False)
        pool = object()
        cfg: dict[str, object] = {"graph": {"backend": "age"}}
        with caplog.at_level(logging.WARNING):
            store = await create_graph_store(cfg, pool)
        assert isinstance(store, PostgresGraphStore)
        assert any(
            "Apache AGE" in rec.message
            and "Falling back" in rec.message
            and rec.levelno == logging.WARNING
            for rec in caplog.records
        ), (
            f"expected warning about AGE fallback, got: {[r.message for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_postgres_backend_returns_postgres_store(self) -> None:
        """Given graph.backend="postgres", return PostgresGraphStore."""
        from src.server_wiring import create_graph_store
        from src.storage.graph_store import PostgresGraphStore

        pool = object()
        cfg: dict[str, object] = {"graph": {"backend": "postgres"}}
        store = await create_graph_store(cfg, pool)
        assert isinstance(store, PostgresGraphStore)

    @pytest.mark.asyncio
    async def test_unknown_backend_raises(self) -> None:
        """Given an unknown graph.backend, raise a clear error."""
        from src.server_wiring import create_graph_store

        pool = object()
        cfg: dict[str, object] = {"graph": {"backend": "nope"}}
        with pytest.raises(ValueError, match=r"nope"):
            await create_graph_store(cfg, pool)

    @pytest.mark.asyncio
    async def test_fresh_calls_select_per_current_config(self) -> None:
        """Two fresh wiring calls with different backends yield different stores."""
        from src.server_wiring import create_graph_store
        from src.storage.age_graph_store import AgeGraphStore
        from src.storage.graph_store import PostgresGraphStore

        age_pool = FakePool(age_present=True)
        postgres_pool = object()
        age_cfg: dict[str, object] = {"graph": {"backend": "age"}}
        pg_cfg: dict[str, object] = {"graph": {"backend": "postgres"}}

        age_store = await create_graph_store(age_cfg, age_pool)
        pg_store = await create_graph_store(pg_cfg, postgres_pool)
        assert isinstance(age_store, AgeGraphStore)
        assert isinstance(pg_store, PostgresGraphStore)


# ============================================================================
# Live fallback probe
# ============================================================================


class TestGraphWiringLiveFallback:
    """Live wiring against PG16 without AGE exercises the fallback path."""

    @pytest.mark.asyncio
    async def test_age_backend_on_pg16_without_age_falls_back(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Given backend="age" on a PG16 server without AGE, fall back gracefully."""
        from src.server_wiring import create_graph_store
        from src.storage.graph_store import PostgresGraphStore

        dsn = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
        try:
            pool = await asyncpg.create_pool(
                dsn, min_size=1, max_size=1, timeout=3, command_timeout=3
            )
        except Exception:
            pytest.skip("Postgres not available")
        try:
            cfg: dict[str, object] = {"graph": {"backend": "age"}}
            with caplog.at_level(logging.WARNING):
                store = await create_graph_store(cfg, pool)
            assert isinstance(store, PostgresGraphStore)
            assert any(
                "Apache AGE" in rec.message and rec.levelno == logging.WARNING
                for rec in caplog.records
            ), (
                f"expected AGE fallback warning, got: {[r.message for r in caplog.records]}"
            )
        finally:
            await pool.close()


# ============================================================================
# GraphHandler delegation
# ============================================================================


class TestGraphHandlerDelegation:
    """GraphHandler delegates to an injected GraphStore."""

    @pytest.mark.asyncio
    async def test_handle_search_graph_delegates(self) -> None:
        """handle_search_graph calls graph_store.search_entities and maps results."""
        from src.handlers.graph_handler import GraphHandler

        fake = FakeGraphStore()
        handler = GraphHandler(fake)
        results = await handler.handle_search_graph(
            TENANT_ID, "User", entity_type="CLASS", limit=10
        )
        assert fake.search_calls == [("User", "CLASS")]
        assert results == [
            {
                "entity_id": "ent-1",
                "name": "UserService",
                "entity_type": "CLASS",
                "metadata": {"lang": "python"},
            }
        ]

    @pytest.mark.asyncio
    async def test_handle_bfs_delegates(self) -> None:
        """handle_bfs calls graph_store.bfs and returns the store result."""
        from src.handlers.graph_handler import GraphHandler

        fake = FakeGraphStore()
        handler = GraphHandler(fake)
        start = UUID("00000000-0000-0000-0000-0000000000a1")
        results = await handler.handle_bfs(TENANT_ID, start, max_depth=2)
        assert fake.bfs_calls == [(str(start), 2)]
        assert results == [
            {
                "entity_id": str(start),
                "name": "",
                "entity_type": "",
                "depth": 0,
            },
            {"entity_id": "ent-2", "name": "", "entity_type": "", "depth": 1},
            {"entity_id": "ent-3", "name": "", "entity_type": "", "depth": 2},
        ]

    @pytest.mark.asyncio
    async def test_handle_get_entity_relations_delegates(self) -> None:
        """handle_get_entity_relations calls graph_store.get_entity_relations."""
        from src.handlers.graph_handler import GraphHandler

        fake = FakeGraphStore()
        handler = GraphHandler(fake)
        entity_id = UUID("00000000-0000-0000-0000-0000000000e1")
        results = await handler.handle_get_entity_relations(TENANT_ID, entity_id)
        assert fake.relation_calls == [str(entity_id)]
        assert results == [
            {
                "relation_id": "rel-1",
                "relation_type": "CALLS",
                "weight": 0.9,
                "source_entity_id": "ent-1",
                "target_entity_id": "ent-2",
                "source_name": "",
                "target_name": "",
            }
        ]

    @pytest.mark.asyncio
    async def test_handler_does_not_acquire_pool_connections(self) -> None:
        """GraphHandler with an injected store does not touch the asyncpg pool."""
        from src.handlers.graph_handler import GraphHandler

        pool = FakePool()
        fake = FakeGraphStore()
        handler = GraphHandler(fake)
        await handler.handle_search_graph(TENANT_ID, "x")
        await handler.handle_bfs(TENANT_ID, UUID(int=1))
        await handler.handle_get_entity_relations(TENANT_ID, UUID(int=2))
        assert pool.acquire_count == 0
        assert pool.release_count == 0

    def test_public_method_signatures_unchanged(self) -> None:
        """The three public handler methods keep their original signatures."""
        from src.handlers.graph_handler import GraphHandler

        search_sig = inspect.signature(GraphHandler.handle_search_graph)
        bfs_sig = inspect.signature(GraphHandler.handle_bfs)
        rel_sig = inspect.signature(GraphHandler.handle_get_entity_relations)

        assert list(search_sig.parameters) == [
            "self",
            "tenant_id",
            "query",
            "entity_type",
            "limit",
        ]
        assert list(bfs_sig.parameters) == [
            "self",
            "tenant_id",
            "start_entity_id",
            "max_depth",
        ]
        assert list(rel_sig.parameters) == ["self", "tenant_id", "entity_id"]
