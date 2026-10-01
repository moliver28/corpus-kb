"""Tests for AgeGraphStore (Apache AGE Cypher backend).

Layers:
  (a) ABC conformance — all 10 GraphStore methods with matching signatures.
  (b) SQL/Cypher construction via an in-memory fake pool (graph name, LOAD
      'age', search_path, parameterized agtype args, no string interpolation).
  (c) Mocked CRUD happy paths returning correct model objects.
  (d) Live degraded probe vs PG16 without AGE — clear, loud error.
  (e) Live AGE-present roundtrip — service-gated skip (deferred to F3 docker).
"""

from __future__ import annotations

import inspect
import json
from typing import Any

import asyncpg
import pytest

from corpus_kb.storage.age_graph_store import AgeGraphStore, AgeUnavailableError
from corpus_kb.storage.graph_store import DEFAULT_TENANT_ID, GraphStore
from corpus_kb.utils.models import Chunk, Document, Entity, Relation

LIVE_DSN = "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb"

TENANT_A = "00000000-0000-0000-0000-00000000000a"
TENANT_B = "00000000-0000-0000-0000-00000000000b"


def agjson(value: object) -> str:
    """Encode a value the way agtype text reaches asyncpg (JSON form)."""
    return json.dumps(value)


# ============================================================================
# Fakes (in-memory, record every call)
# ============================================================================


class FakeTransaction:
    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeConnection:
    """asyncpg.Connection fake: records calls, routes canned fetch responses."""

    def __init__(self, *, age_present: bool = True) -> None:
        self.age_present = age_present
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.fetch_result: list[dict[str, Any]] = []
        self.fetchrow_result: dict[str, Any] | None = None
        self.fetch_responses: list[tuple[str, list[dict[str, Any]]]] = []

    def add_fetch_response(self, needle: str, rows: list[dict[str, Any]]) -> None:
        self.fetch_responses.append((needle, rows))

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append(("execute", sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append(("fetchrow", sql, args))
        if "pg_extension" in sql:
            return {"ok": self.age_present}
        if "ag_graph" in sql:
            return {"ok": self.age_present}
        return self.fetchrow_result

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append(("fetch", sql, args))
        for needle, rows in self.fetch_responses:
            if needle in sql:
                return rows
        return self.fetch_result

    def transaction(self) -> FakeTransaction:
        return FakeTransaction()


class FakePool:
    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn
        self.acquire_count = 0
        self.release_count = 0

    async def acquire(self) -> FakeConnection:
        self.acquire_count += 1
        return self._conn

    async def release(self, conn: FakeConnection) -> None:
        self.release_count += 1


@pytest.fixture
def conn() -> FakeConnection:
    return FakeConnection()


@pytest.fixture
def pool(conn: FakeConnection) -> FakePool:
    return FakePool(conn)


@pytest.fixture
def store(pool: FakePool) -> AgeGraphStore:
    return AgeGraphStore(pool)


def make_entity(name: str = "Widget", entity_type: str = "CLASS") -> Entity:
    return Entity(name=name, entity_type=entity_type, source_type="code")


def cypher_calls(conn: FakeConnection) -> list[tuple[str, tuple[Any, ...]]]:
    return [(sql, args) for (m, sql, args) in conn.calls if "cypher(" in sql]


# ============================================================================
# (a) ABC conformance
# ============================================================================


def test_all_ten_methods_exist_with_abc_conformant_signatures() -> None:
    assert issubclass(AgeGraphStore, GraphStore)
    expected = [
        "add_entity",
        "add_relation",
        "get_entity",
        "search_entities",
        "get_entity_relations",
        "bfs",
        "close",
        "transaction",
        "add_document",
        "add_chunk",
    ]
    for name in expected:
        assert name in AgeGraphStore.__dict__, f"{name} not implemented"
        impl_sig = inspect.signature(getattr(AgeGraphStore, name))
        abc_sig = inspect.signature(getattr(GraphStore, name))
        assert list(impl_sig.parameters.values()) == list(abc_sig.parameters.values()), (
            f"{name} parameters diverge from GraphStore ABC"
        )
        assert impl_sig.return_annotation == abc_sig.return_annotation, (
            f"{name} return annotation diverges from GraphStore ABC"
        )


def test_age_graph_store_instantiates(pool: FakePool) -> None:
    store = AgeGraphStore(pool)
    assert store is not None


# ============================================================================
# (b) Connection setup + cypher/SQL construction
# ============================================================================


async def test_connection_setup_loads_age_and_sets_search_path(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    await store.search_entities("x")
    executed = [sql for (m, sql, _a) in conn.calls if m == "execute"]
    assert "LOAD 'age'" in executed
    assert 'SET search_path = ag_catalog, "", public' in executed
    assert any("set_config('app.current_tenant_id'" in sql for sql in executed)
    probed = [sql for (m, sql, _a) in conn.calls if m == "fetchrow"]
    assert any("pg_extension" in sql for sql in probed)
    assert any("ag_catalog.ag_graph" in sql for sql in probed)


async def test_cypher_calls_use_corpus_kb_graph_and_agtype_params(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    await store.search_entities("Widget", entity_type="CLASS")
    calls = cypher_calls(conn)
    assert calls, "expected at least one cypher() call"
    for sql, args in calls:
        assert "cypher('corpus_kb'" in sql
        assert "$$" in sql
        assert "$1::agtype" in sql
        assert len(args) == 1
    params = json.loads(calls[-1][1][0])
    assert params["tenant_id"] == DEFAULT_TENANT_ID
    assert params["name"] == "Widget"
    assert params["entity_type"] == "CLASS"


async def test_search_entities_does_not_interpolate_untrusted_input(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    malicious = "x'); DROP TABLE entities; --"
    await store.search_entities(malicious)
    sql, args = cypher_calls(conn)[-1]
    assert malicious not in sql
    params = json.loads(args[0])
    assert params["name"] == malicious


async def test_bfs_does_not_interpolate_untrusted_start_id(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    malicious = "1 OR 1=1; DELETE"
    await store.bfs(malicious, max_depth=2)
    for sql, args in cypher_calls(conn):
        assert malicious not in sql
        assert json.loads(args[0])["start_id"] == malicious


@pytest.mark.parametrize("bad_depth", ["abc", "1; DROP TABLE", "", None, 0, -5, 26])
async def test_bfs_depth_is_int_cast_and_bounded(store: AgeGraphStore, bad_depth: object) -> None:
    with pytest.raises(ValueError):
        await store.bfs("eid", max_depth=bad_depth)


# ============================================================================
# (c) Mocked CRUD happy paths
# ============================================================================


async def test_add_entity_merges_and_returns_id(conn: FakeConnection, store: AgeGraphStore) -> None:
    entity = make_entity()
    conn.fetch_result = [{"entity_id": agjson(entity.entity_id)}]
    result = await store.add_entity(entity)
    assert result == entity.entity_id
    sql, args = cypher_calls(conn)[-1]
    assert "MERGE (e:Entity" in sql
    assert "ON CREATE SET" in sql
    params = json.loads(args[0])
    assert params["name"] == "Widget"
    assert params["entity_type"] == "CLASS"
    assert params["tenant_id"] == DEFAULT_TENANT_ID


async def test_add_relation_merges_between_matched_entities(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    relation = Relation(source_entity_id="src-1", target_entity_id="tgt-1", relation_type="CALLS")
    conn.fetch_result = [{"relation_id": agjson(relation.relation_id)}]
    result = await store.add_relation(relation)
    assert result == relation.relation_id
    sql, args = cypher_calls(conn)[-1]
    assert "MATCH (s:Entity), (t:Entity)" in sql
    assert "MERGE (s)-[r:REL" in sql
    params = json.loads(args[0])
    assert params["source_entity_id"] == "src-1"
    assert params["target_entity_id"] == "tgt-1"
    assert params["relation_type"] == "CALLS"
    assert params["weight"] == 1.0


async def test_add_relation_missing_endpoints_raises(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    relation = Relation(
        source_entity_id="ghost-1", target_entity_id="ghost-2", relation_type="CALLS"
    )
    conn.fetch_result = []
    with pytest.raises(ValueError, match="not found"):
        await store.add_relation(relation)


async def test_get_entity_returns_model(conn: FakeConnection, store: AgeGraphStore) -> None:
    conn.fetch_result = [
        {
            "entity_id": agjson("eid-1"),
            "name": agjson("Widget"),
            "entity_type": agjson("CLASS"),
            "metadata": agjson({"k": "v"}),
            "source_document_id": agjson("doc-9"),
        }
    ]
    entity = await store.get_entity("eid-1")
    assert entity is not None
    assert entity.entity_id == "eid-1"
    assert entity.name == "Widget"
    assert entity.entity_type == "CLASS"
    assert entity.metadata == {"k": "v"}
    assert entity.source_document_id == "doc-9"


async def test_get_entity_returns_none_when_absent(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    conn.fetch_result = []
    assert await store.get_entity("missing") is None


async def test_search_entities_returns_models_case_insensitive(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    conn.fetch_result = [
        {
            "entity_id": agjson("eid-1"),
            "name": agjson("WidgetFactory"),
            "entity_type": agjson("CLASS"),
            "metadata": agjson({}),
            "source_document_id": agjson(None),
        }
    ]
    results = await store.search_entities("widget")
    assert len(results) == 1
    assert results[0].name == "WidgetFactory"
    sql, _args = cypher_calls(conn)[-1]
    assert "toLower" in sql
    assert "CONTAINS" in sql


async def test_get_entity_relations_returns_models(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    conn.fetch_result = [
        {
            "relation_id": agjson("rel-1"),
            "source_entity_id": agjson("eid-1"),
            "target_entity_id": agjson("eid-2"),
            "relation_type": agjson("CALLS"),
            "weight": agjson(2.5),
            "metadata": agjson({}),
        }
    ]
    relations = await store.get_entity_relations("eid-1")
    assert len(relations) == 1
    assert relations[0].relation_id == "rel-1"
    assert relations[0].weight == 2.5
    sql, _args = cypher_calls(conn)[-1]
    assert "MATCH (e:Entity)-[r:REL]" in sql


async def test_bfs_returns_visited_with_min_depths(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    conn.add_fetch_response("RETURN a.entity_id AS eid", [{"eid": agjson("start")}])
    conn.add_fetch_response(
        "min(length(p))",
        [
            {"eid": agjson("b"), "depth": agjson(2)},
            {"eid": agjson("b"), "depth": agjson(1)},
            {"eid": agjson("c"), "depth": agjson(2)},
        ],
    )
    result = await store.bfs("start", max_depth=3)
    assert result == {
        "start_entity_id": "start",
        "max_depth": 3,
        "visited": {"start": 0, "b": 1, "c": 2},
    }
    traversal = [sql for sql, _a in cypher_calls(conn) if "length(p)" in sql][-1]
    assert "[:REL*1..3]" in traversal


async def test_bfs_unknown_start_returns_empty_visited(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    conn.fetch_result = []
    result = await store.bfs("ghost", max_depth=2)
    assert result == {"start_entity_id": "ghost", "max_depth": 2, "visited": {}}


async def test_add_document_upserts_with_on_conflict(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    document = Document(path="/src/x.py", source_type="code", content="...", size_bytes=10)
    conn.fetchrow_result = {"doc_id": document.document_id}
    result = await store.add_document(document)
    assert result == document.document_id
    inserts = [sql for (m, sql, _a) in conn.calls if "INSERT INTO documents" in sql]
    assert inserts
    assert "ON CONFLICT" in inserts[-1]


async def test_add_chunk_uses_sibling_order_as_index(
    conn: FakeConnection, store: AgeGraphStore
) -> None:
    chunk = Chunk(document_id="doc-1", text="def f(): pass", source_type="code", sibling_order=7)
    conn.fetchrow_result = {"chunk_id": chunk.chunk_id}
    result = await store.add_chunk(chunk)
    assert result == chunk.chunk_id
    inserts = [(sql, args) for (m, sql, args) in conn.calls if "INSERT INTO chunks" in sql]
    assert inserts
    assert inserts[-1][1][3] == 7


async def test_transaction_wraps_writes_on_one_connection(
    conn: FakeConnection, pool: FakePool, store: AgeGraphStore
) -> None:
    async with store.transaction():
        await store.add_entity(make_entity())
        await store.add_entity(make_entity("Other"))
    assert pool.acquire_count == 1
    assert pool.release_count == 1
    tenant_sets = [args for (m, sql, args) in conn.calls if "set_config" in sql]
    assert len(tenant_sets) == 1


async def test_close_is_noop(pool: FakePool, store: AgeGraphStore) -> None:
    await store.close()
    assert pool.acquire_count == 0


# ============================================================================
# Adversarial: tenant context must not leak across pooled connections
# ============================================================================


async def test_tenant_context_scoped_per_store(conn: FakeConnection, pool: FakePool) -> None:
    store_a = AgeGraphStore(pool, tenant_id=TENANT_A)
    store_b = AgeGraphStore(pool, tenant_id=TENANT_B)
    await store_a.add_entity(make_entity())
    await store_b.search_entities("Widget")
    tenant_sets = [args for (m, sql, args) in conn.calls if "set_config" in sql]
    assert tenant_sets[0] == (TENANT_A,)
    assert tenant_sets[-1] == (TENANT_B,)
    write_sql, write_args = [
        (sql, args) for sql, args in cypher_calls(conn) if "MERGE (e:Entity" in sql
    ][-1]
    read_sql, read_args = cypher_calls(conn)[-1]
    assert json.loads(write_args[0])["tenant_id"] == TENANT_A
    assert json.loads(read_args[0])["tenant_id"] == TENANT_B
    assert TENANT_A not in read_sql and TENANT_B not in write_sql


# ============================================================================
# (d) Live degraded probe — PG16 without AGE must raise a clear error
# ============================================================================


async def test_live_degraded_probe_raises_clear_error_without_age() -> None:
    try:
        pool = await asyncpg.create_pool(LIVE_DSN, min_size=1, max_size=1, timeout=3)
    except Exception:
        pytest.skip("Postgres not available on localhost:5432")
    try:
        async with pool.acquire() as conn:
            age_present = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'age')"
            )
        if age_present:
            pytest.skip("AGE present — degraded contract covered by mocked tests")
        store = AgeGraphStore(pool)
        with pytest.raises(AgeUnavailableError) as excinfo:
            await store.search_entities("anything")
        message = str(excinfo.value).lower()
        assert "apache age" in message
        assert "extension" in message
        assert "corpus_kb" in message
        assert "postgresgraphstore" in message
    finally:
        await pool.close()


# ============================================================================
# (e) Live AGE-present roundtrip — service-gated (deferred to F3 docker)
# ============================================================================


async def test_live_age_roundtrip_when_available() -> None:
    try:
        pool = await asyncpg.create_pool(LIVE_DSN, min_size=1, max_size=2, timeout=3)
    except Exception:
        pytest.skip("Postgres not available on localhost:5432")
    try:
        async with pool.acquire() as conn:
            age_present = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'age')"
            )
        if not age_present:
            pytest.skip(
                "Apache AGE not installed — live cypher execution deferred to "
                "F3 docker (PG17 + AGE)"
            )
        store = AgeGraphStore(pool)
        e1 = make_entity("AgeRoundtripA")
        e2 = make_entity("AgeRoundtripB")
        eid1 = await store.add_entity(e1)
        eid2 = await store.add_entity(e2)
        assert await store.add_entity(e1) == eid1  # upsert is stable
        fetched = await store.get_entity(eid1)
        assert fetched is not None and fetched.name == "AgeRoundtripA"
        relation = Relation(source_entity_id=eid1, target_entity_id=eid2, relation_type="CALLS")
        rid = await store.add_relation(relation)
        relations = await store.get_entity_relations(eid1)
        assert any(r.relation_id == rid for r in relations)
        found = await store.search_entities("AgeRoundtrip")
        assert {e.name for e in found} >= {"AgeRoundtripA", "AgeRoundtripB"}
        bfs = await store.bfs(eid1, max_depth=2)
        assert bfs["visited"][eid1] == 0
        assert bfs["visited"][eid2] == 1
    finally:
        await pool.close()
