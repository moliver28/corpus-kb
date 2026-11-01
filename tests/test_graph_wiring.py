"""Tests for graph store wiring and GraphHandler delegation.

Covers the seams defined by todo 2b, plus the storage-portability wiring
(program §3.4, sp-duckdb-store):
  - server_wiring.create_graph_store selects the backend by config.
  - backend="age" with AGE unavailable falls back to PostgresGraphStore
    and logs a warning.
  - backend="postgres" selects PostgresGraphStore directly.
  - unknown backend raises a clear error at wiring time.
  - database.backend="duckdb" selects DuckDbGraphStore over the
    configured file; an age+duckdb engine mix is refused with the banked
    remediation (the Wave A mix gate, surfaced at wiring).
  - startup() reads database.backend once: the pool exists only on the
    Postgres path, the event store is keyed on the backend, and a duckdb
    selection is refused with a typed error until the stack ports.
  - GraphHandler delegates entity/relation queries to the injected GraphStore
    while preserving public method signatures and return shapes.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import pytest

from corpus_kb.server_wiring import create_graph_store
from corpus_kb.storage.age_graph_store import AgeUnavailableError
from corpus_kb.storage.graph_store import DEFAULT_TENANT_ID, GraphStore
from corpus_kb.utils.models import Entity, Relation

TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_TENANT = UUID("00000000-0000-0000-0000-000000000002")


# ============================================================================
# Fakes
# ============================================================================


class FakeTransaction:
    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakePool:
    """asyncpg.Pool fake that counts acquire/release and yields FakeConnections."""

    def __init__(self, *, age_present: bool = True) -> None:
        self.age_present = age_present
        self.acquire_count = 0
        self.release_count = 0

    async def acquire(self) -> FakeConnection:
        self.acquire_count += 1
        return FakeConnection(pool=self)

    async def release(self, conn: FakeConnection) -> None:
        self.release_count += 1


class FakeConnection:
    """asyncpg.Connection fake for AGE probe checks."""

    def __init__(self, *, pool: FakePool | None = None) -> None:
        self.pool = pool

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
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
        self.search_calls: list[tuple[str, str | None]] = []
        self.bfs_calls: list[tuple[str, int]] = []
        self.relation_calls: list[str] = []
        self.neighbor_calls: list[tuple[str, int, str, int]] = []
        self.traverse_calls: list[tuple[str, int, int]] = []
        self.closed = False

    async def add_entity(self, entity: Entity) -> str:
        return entity.entity_id

    async def add_relation(self, relation: Relation) -> str:
        return relation.relation_id

    async def get_entity(self, entity_id: str) -> Entity | None:
        return None

    async def search_entities(self, name: str, entity_type: str | None = None) -> list[Entity]:
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

    async def neighbors(
        self,
        entity_id: str,
        hops: int = 1,
        direction: str = "both",
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        self.neighbor_calls.append((entity_id, hops, direction, limit))
        return [
            {
                "entity_id": "ent-2",
                "name": "EntityTwo",
                "entity_type": "CLASS",
                "metadata": {},
                "depth": 1,
            }
        ]

    async def traverse(
        self,
        entity_id: str,
        max_depth: int = 5,
        relation_types: list[str] | None = None,
        entity_types: list[str] | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        self.traverse_calls.append((entity_id, max_depth, limit))
        return [
            {
                "entity_id": "ent-2",
                "name": "EntityTwo",
                "entity_type": "CLASS",
                "metadata": {},
                "depth": 1,
            }
        ]

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
        from corpus_kb.server_wiring import create_graph_store
        from corpus_kb.storage.age_graph_store import AgeGraphStore

        pool = FakePool(age_present=True)
        cfg: dict[str, object] = {"graph": {"backend": "age"}}
        store = await create_graph_store(cfg, pool)
        assert isinstance(store, AgeGraphStore)

    @pytest.mark.asyncio
    async def test_age_backend_unavailable_falls_back_with_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Given graph.backend="age" but AGE absent, fall back and warn."""
        from corpus_kb.server_wiring import create_graph_store
        from corpus_kb.storage.graph_store import PostgresGraphStore

        class RaisingAgeStore(GraphStore):
            def __init__(self, pool: object, tenant_id: str = DEFAULT_TENANT_ID) -> None:
                raise AgeUnavailableError("age not installed")

            async def add_entity(self, entity: Entity) -> str:
                return ""

            async def add_relation(self, relation: Relation) -> str:
                return ""

            async def get_entity(self, entity_id: str) -> Entity | None:
                return None

            async def search_entities(
                self, name: str, entity_type: str | None = None
            ) -> list[Entity]:
                return []

            async def get_entity_relations(self, entity_id: str) -> list[Relation]:
                return []

            async def bfs(self, start_entity_id: str, max_depth: int = 5) -> dict[str, object]:
                return {}

            async def neighbors(
                self,
                entity_id: str,
                hops: int = 1,
                direction: str = "both",
                relation_types: list[str] | None = None,
                entity_types: list[str] | None = None,
                limit: int = 100,
            ) -> list[dict[str, object]]:
                return []

            async def traverse(
                self,
                entity_id: str,
                max_depth: int = 5,
                relation_types: list[str] | None = None,
                entity_types: list[str] | None = None,
                limit: int = 100,
            ) -> list[dict[str, object]]:
                return []

            async def close(self) -> None:
                pass

            def transaction(self):
                raise NotImplementedError

            async def add_document(self, document: Any) -> str:
                return ""

            async def add_chunk(self, chunk: Any) -> str:
                return ""

        monkeypatch.setattr("corpus_kb.storage.AgeGraphStore", RaisingAgeStore, raising=False)
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
        ), f"expected warning about AGE fallback, got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_postgres_backend_returns_postgres_store(self) -> None:
        """Given graph.backend="postgres", return PostgresGraphStore."""
        from corpus_kb.server_wiring import create_graph_store
        from corpus_kb.storage.graph_store import PostgresGraphStore

        pool = object()
        cfg: dict[str, object] = {"graph": {"backend": "postgres"}}
        store = await create_graph_store(cfg, pool)
        assert isinstance(store, PostgresGraphStore)

    @pytest.mark.asyncio
    async def test_unknown_backend_raises(self) -> None:
        """Given an unknown graph.backend, raise a clear error."""
        from corpus_kb.server_wiring import create_graph_store

        pool = object()
        cfg: dict[str, object] = {"graph": {"backend": "nope"}}
        with pytest.raises(ValueError, match=r"nope"):
            await create_graph_store(cfg, pool)

    @pytest.mark.asyncio
    async def test_fresh_calls_select_per_current_config(self) -> None:
        """Two fresh wiring calls with different backends yield different stores."""
        from corpus_kb.server_wiring import create_graph_store
        from corpus_kb.storage.age_graph_store import AgeGraphStore
        from corpus_kb.storage.graph_store import PostgresGraphStore

        age_pool = FakePool(age_present=True)
        postgres_pool = object()
        age_cfg: dict[str, object] = {"graph": {"backend": "age"}}
        pg_cfg: dict[str, object] = {"graph": {"backend": "postgres"}}

        age_store = await create_graph_store(age_cfg, age_pool)
        pg_store = await create_graph_store(pg_cfg, postgres_pool)
        assert isinstance(age_store, AgeGraphStore)
        assert isinstance(pg_store, PostgresGraphStore)


# ============================================================================
# DuckDB wiring (storage portability program, sp-duckdb-store)
# ============================================================================


class TestCreateGraphStoreDuckDb:
    """database.backend="duckdb" wires DuckDbGraphStore; mixes are refused."""

    @pytest.mark.asyncio
    async def test_duckdb_backend_wires_store_over_tmp_file(self, tmp_path: Path) -> None:
        """Given the duckdb backend and a migrated tmp file, return DuckDbGraphStore."""
        duckdb = pytest.importorskip("duckdb")
        from corpus_kb._setup.migrate import run_migrations
        from corpus_kb.storage.duckdb_store import DuckDbGraphStore
        from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

        path = tmp_path / "wiring.duckdb"
        await run_migrations(duckdb_path_to_conn_string(path))
        conn = duckdb.connect(str(path))
        try:
            cfg: dict[str, object] = {"database": {"backend": "duckdb"}}
            store = await create_graph_store(cfg, conn)
            assert isinstance(store, DuckDbGraphStore)
            # The wired store serves the read ops, not just the type.
            entity = Entity(
                entity_id="00000000-0000-0000-0000-00000000a1e1",
                name="Wired",
                entity_type="CLASS",
                source_type="code",
            )
            await store.add_entity(entity)
            found = await store.search_entities("Wired")
            assert [item.name for item in found] == ["Wired"]
        finally:
            # Rule 13: close before anything could remove the file.
            conn.close()

    @pytest.mark.asyncio
    async def test_duckdb_backend_opens_the_configured_file(self) -> None:
        """Given no connection, the factory opens database.connection_string."""
        from corpus_kb.storage.duckdb_store import DuckDbGraphStore

        cfg: dict[str, object] = {
            "database": {"backend": "duckdb", "connection_string": "duckdb:///:memory:"}
        }
        store = await create_graph_store(cfg)
        assert isinstance(store, DuckDbGraphStore)
        # In-memory: no file to close; the connection dies with the store.

    @pytest.mark.asyncio
    async def test_age_on_duckdb_mix_is_refused_with_banked_remediation(self) -> None:
        """The Wave A mix gate surfaces at wiring, not deep in the store."""
        from corpus_kb.config import AGE_ON_DUCKDB_ERROR
        from corpus_kb.errors import ConfigError

        cfg: dict[str, object] = {
            "database": {"backend": "duckdb"},
            "graph": {"backend": "age"},
        }
        with pytest.raises(ConfigError) as excinfo:
            await create_graph_store(cfg)
        assert str(excinfo.value) == AGE_ON_DUCKDB_ERROR

    @pytest.mark.asyncio
    async def test_pgml_on_duckdb_mix_is_refused(self) -> None:
        """Any Postgres-only engine selection dies at the factory, too."""
        from corpus_kb.errors import ConfigError

        cfg: dict[str, object] = {
            "database": {"backend": "duckdb"},
            "embedding": {"provider": "pgml"},
        }
        with pytest.raises(ConfigError, match="Ollama embedder"):
            await create_graph_store(cfg)

    @pytest.mark.asyncio
    async def test_unknown_database_backend_raises(self) -> None:
        """An unknown database.backend fails loudly at wiring time."""
        cfg: dict[str, object] = {"database": {"backend": "nope"}}
        with pytest.raises(ValueError, match=r"nope"):
            await create_graph_store(cfg, object())

    @pytest.mark.asyncio
    async def test_database_backend_defaults_to_postgres(self) -> None:
        """No database.backend key: the postgres path (age probe) applies."""
        from corpus_kb.storage.age_graph_store import AgeGraphStore

        pool = FakePool(age_present=True)
        store = await create_graph_store({}, pool)
        assert isinstance(store, AgeGraphStore)

    def test_server_wiring_composes_backend_once(self) -> None:
        """The wiring reads the backend once and closes the max_hops gap.

        sp-hops noted the wired server still constructed GraphHandler
        without config (the traversal ceiling stayed at the default 5).
        The composer must pass the config down and key the event store on
        the backend — pinned here as source, since booting the full stack
        in a unit test needs a live Postgres.
        """
        import inspect

        from corpus_kb import server_wiring

        source = inspect.getsource(server_wiring)
        assert 'backend = str(db_cfg.get("backend") or "postgres")' in source
        assert "get_app(conn_str, backend=backend)" in source
        assert "GraphHandler(graph_store, config=cfg)" in source
        # The pool exists only on the Postgres path.
        assert 'if backend != "postgres":' in source

    @pytest.mark.asyncio
    async def test_startup_on_duckdb_wires_the_research_ecosystem(self, tmp_path) -> None:
        """A duckdb startup builds the gateway research deps (sp-research-port).

        The event store is the SQLite recorder, the SQL components ride the
        one gateway, and the not-yet-ported handler surfaces stay unset
        (they fail typed per route, never at startup).
        """
        from corpus_kb.server_wiring import startup
        from corpus_kb.storage.gateway import DuckDbGateway

        cfg: dict[str, object] = {
            "database": {
                "backend": "duckdb",
                "connection_string": f"duckdb:///{(tmp_path / 'wiring.duckdb').as_posix()}",
            }
        }
        services = await startup(config=cfg)
        try:
            assert isinstance(services["gateway"], DuckDbGateway)
            assert services["pool"] is None
            assert services["event_reader"] is not None
            assert services["docs_projection"] is not None
            assert services["embed_projection"] is not None
            assert services["http_app"] is not None
            assert services.get("query_handler") is None
        finally:
            gateway = services["gateway"]
            if isinstance(gateway, DuckDbGateway):
                await gateway.close()


# ============================================================================
# Live fallback probe
# ============================================================================


class TestGraphWiringLiveFallback:
    """Live wiring against PG16 without AGE exercises the fallback path."""

    @pytest.mark.asyncio
    @pytest.mark.requires_postgres
    async def test_age_backend_on_pg16_without_age_falls_back(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Given backend="age" on a PG16 server without AGE, fall back gracefully."""
        from corpus_kb.server_wiring import create_graph_store
        from corpus_kb.storage.graph_store import PostgresGraphStore

        dsn = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, timeout=3, command_timeout=3)
        try:
            # The premise is "this server has no AGE". The bundled CI image
            # ships the extension (and the bootstrap creates the graph), so
            # there the store is rightly a live AgeGraphStore and this
            # fallback premise does not apply.
            if await pool.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'age')"
            ):
                pytest.skip("server has the age extension; the fallback premise does not apply")
            cfg: dict[str, object] = {"graph": {"backend": "age"}}
            with caplog.at_level(logging.WARNING):
                store = await create_graph_store(cfg, pool)
            assert isinstance(store, PostgresGraphStore)
            assert any(
                "Apache AGE" in rec.message and rec.levelno == logging.WARNING
                for rec in caplog.records
            ), f"expected AGE fallback warning, got: {[r.message for r in caplog.records]}"
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
        from corpus_kb.handlers.graph_handler import GraphHandler

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
        from corpus_kb.handlers.graph_handler import GraphHandler

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
        from corpus_kb.handlers.graph_handler import GraphHandler

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
        from corpus_kb.handlers.graph_handler import GraphHandler

        pool = FakePool()
        fake = FakeGraphStore()
        handler = GraphHandler(fake)
        await handler.handle_search_graph(TENANT_ID, "x")
        await handler.handle_bfs(TENANT_ID, UUID(int=1))
        await handler.handle_get_entity_relations(TENANT_ID, UUID(int=2))
        assert pool.acquire_count == 0
        assert pool.release_count == 0

    @pytest.mark.asyncio
    async def test_handle_graph_neighbors_delegates(self) -> None:
        """handle_graph_neighbors validates and routes to store.neighbors."""
        from corpus_kb.handlers.graph_handler import GraphHandler

        fake = FakeGraphStore()
        handler = GraphHandler(fake)
        entity_id = UUID("00000000-0000-0000-0000-0000000000e1")
        envelope = await handler.handle_graph_neighbors(
            TENANT_ID, entity_id, hops=2, direction="out", limit=10
        )
        assert fake.neighbor_calls == [(str(entity_id), 2, "out", 10)]
        assert envelope == {
            "status": "success",
            "result": [
                {
                    "entity_id": "ent-2",
                    "name": "EntityTwo",
                    "entity_type": "CLASS",
                    "metadata": {},
                    "depth": 1,
                }
            ],
        }

    @pytest.mark.asyncio
    async def test_handle_graph_neighbors_rejects_bad_input(self) -> None:
        """hops past the fast path and unknown directions are input errors."""
        from corpus_kb.errors import ValidationError
        from corpus_kb.handlers.graph_handler import GraphHandler

        fake = FakeGraphStore()
        handler = GraphHandler(fake)
        entity_id = UUID(int=3)
        with pytest.raises(ValidationError):
            await handler.handle_graph_neighbors(TENANT_ID, entity_id, hops=4)
        with pytest.raises(ValidationError):
            await handler.handle_graph_neighbors(TENANT_ID, entity_id, direction="up")
        assert fake.neighbor_calls == []

    @pytest.mark.asyncio
    async def test_handle_graph_traverse_fast_path_under_three_hops(self) -> None:
        """Depths 1..3 route to store.neighbors and fetch one extra row."""
        from corpus_kb.handlers.graph_handler import GraphHandler

        fake = FakeGraphStore()
        handler = GraphHandler(fake)
        entity_id = UUID(int=4)
        envelope = await handler.handle_graph_traverse(TENANT_ID, entity_id, max_depth=3)
        assert fake.traverse_calls == []
        assert fake.neighbor_calls == [(str(entity_id), 3, "both", 101)]
        assert envelope["status"] == "success"
        assert envelope["truncated"] is False

    @pytest.mark.asyncio
    async def test_handle_graph_traverse_deep_walks_and_reports_truncation(self) -> None:
        """Depths past 3 route to store.traverse; an extra row pins truncated."""
        from corpus_kb.handlers.graph_handler import GraphHandler

        fake = FakeGraphStore()
        handler = GraphHandler(fake, config={"graph": {"max_hops": 5}})
        entity_id = UUID(int=5)
        envelope = await handler.handle_graph_traverse(TENANT_ID, entity_id, max_depth=5)
        assert fake.neighbor_calls == []
        assert fake.traverse_calls == [(str(entity_id), 5, 101)]
        assert envelope["truncated"] is False

        class _DeepStore(FakeGraphStore):
            async def traverse(
                self,
                entity_id: str,
                max_depth: int = 5,
                relation_types: list[str] | None = None,
                entity_types: list[str] | None = None,
                limit: int = 100,
            ) -> list[dict[str, object]]:
                self.traverse_calls.append((entity_id, max_depth, limit))
                return [
                    {"entity_id": "x", "depth": d, "name": "", "entity_type": "", "metadata": {}}
                    for d in range(limit)
                ]

        deep = GraphHandler(_DeepStore(), config={"graph": {"max_hops": 5}})
        filled = await deep.handle_graph_traverse(TENANT_ID, entity_id, max_depth=5, limit=100)
        assert filled["truncated"] is True
        assert len(filled["result"]) == 100

    @pytest.mark.asyncio
    async def test_handle_graph_traverse_rejects_depth_past_ceiling(self) -> None:
        """max_depth above graph.max_hops is an input error, not a walk."""
        from corpus_kb.errors import ValidationError
        from corpus_kb.handlers.graph_handler import GraphHandler

        handler = GraphHandler(FakeGraphStore(), config={"graph": {"max_hops": 5}})
        with pytest.raises(ValidationError):
            await handler.handle_graph_traverse(TENANT_ID, UUID(int=6), max_depth=6)
        with pytest.raises(ValidationError):
            await handler.handle_graph_traverse(TENANT_ID, UUID(int=6), max_depth=1)

    def test_configured_max_hops_clamps(self) -> None:
        """graph.max_hops resolves, defaults, and clamps to 1..25."""
        from corpus_kb.handlers.graph_handler import configured_max_hops

        assert configured_max_hops(None) == 5
        assert configured_max_hops({}) == 5
        assert configured_max_hops({"graph": {"max_hops": 12}}) == 12
        assert configured_max_hops({"graph": {"max_hops": 99}}) == 25
        assert configured_max_hops({"graph": {"max_hops": 0}}) == 1
        assert configured_max_hops({"graph": {"max_hops": "nonsense"}}) == 5

    def test_public_method_signatures_unchanged(self) -> None:
        """The public handler methods keep their catalogue-facing signatures."""
        from corpus_kb.handlers.graph_handler import GraphHandler

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
        # The §5.1 birth adds the optional limit; None keeps the uncapped read.
        assert list(rel_sig.parameters) == ["self", "tenant_id", "entity_id", "limit"]
