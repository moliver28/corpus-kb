"""Behavioral coverage for the issue-#330 both-engines arms.

Each test pins a runtime arm the big-suite files reach only under a
wired server: the store-opener error arms, the schema probes on both
engines, the delete-cascade planner guards and cycle refusal, the graph
store's provenance writes, the DuckDB doctor branch, and the lazy
handler builds the CLI/tools fall back to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.errors import ConfigError, DatabaseUnavailableError, InternalError
from corpus_kb.storage.duckdb_cascade_plan import (
    Edge,
    doomed_pred,
    doomed_tables,
    self_reference,
    set_null_sql,
)
from corpus_kb.storage.gateway import DuckDbGateway

if TYPE_CHECKING:
    from pathlib import Path

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture()
async def migrated(duckdb_path: Path) -> Any:
    run_duckdb_migrations(f"duckdb:///{duckdb_path.as_posix()}")
    gateway = await DuckDbGateway.connect(f"duckdb:///{duckdb_path.as_posix()}")
    yield gateway
    await gateway.close()


# --- storage.connect error arms ----------------------------------------------


async def test_open_store_refuses_an_empty_duckdb_dsn() -> None:
    from corpus_kb.storage.connect import open_store

    with pytest.raises(ConfigError, match="names no file"):
        await open_store("duckdb:///")


async def test_open_store_refuses_an_empty_postgres_dsn() -> None:
    from corpus_kb.storage.connect import open_store

    with pytest.raises(ConfigError, match="No database connection string"):
        await open_store("")


async def test_open_store_maps_a_postgres_connect_failure() -> None:
    from corpus_kb.storage.connect import open_store

    with pytest.raises(DatabaseUnavailableError, match="Failed to connect"):
        await open_store("postgresql://nobody:nope@127.0.0.1:1/none")


def test_pg_pool_of_answers_none_on_the_gateway() -> None:
    from corpus_kb.storage.connect import pg_pool_of

    assert pg_pool_of(None) is None


def test_resolve_event_backend_prefers_the_explicit_choice() -> None:
    from corpus_kb.storage.connect import resolve_event_backend

    assert resolve_event_backend("duckdb:///x", "postgres") == "postgres"
    assert resolve_event_backend("duckdb:///x", None) == "duckdb"
    assert resolve_event_backend("postgresql://x", None) == "postgres"


# --- schema_probe on both engines ---------------------------------------------


class _FakeConn:
    """A held reader: answers fetchval from a fixed table."""

    def __init__(self, answers: dict[str, object]) -> None:
        self._answers = answers

    async def fetchval(self, sql: str, *args: object) -> object:
        for probe, answer in self._answers.items():
            if probe in sql:
                return answer
        return 0


class _FakePool:
    """The pool arm: acquire yields the reader (one engine-agnostic probe)."""

    def __init__(self, answers: dict[str, object]) -> None:
        self._answers = answers

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _FakeConn:
        return _FakeConn(self._answers)

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_tables_exist_pool_arm_interrogates_the_acquired_connection() -> None:
    from corpus_kb.storage.schema_probe import tables_exist

    hit: dict[str, object] = {"to_regclass": 1}
    assert await tables_exist(cast("Any", _FakePool(hit)), "documents") is True
    assert await tables_exist(cast("Any", _FakePool({})), "documents") is False


async def test_column_exists_pool_and_connection_arms() -> None:
    from corpus_kb.storage.schema_probe import column_exists

    hit: dict[str, object] = {"information_schema.columns": 1}
    assert await column_exists(cast("Any", _FakePool(hit)), "documents", "document_date") is True
    assert await column_exists(cast("Any", _FakeConn(hit)), "documents", "document_date") is True
    assert await column_exists(cast("Any", _FakeConn({})), "documents", "document_date") is False


async def test_tables_exist_gateway_arm_counts_the_named_tables(migrated: Any) -> None:
    from corpus_kb.storage.schema_probe import tables_exist

    assert await tables_exist(migrated, "documents", "chunks") is True
    assert await tables_exist(migrated, "documents", "no_such_table") is False


async def test_column_exists_gateway_arm(migrated: Any) -> None:
    from corpus_kb.storage.schema_probe import column_exists

    assert await column_exists(migrated, "documents", "doc_id") is True
    assert await column_exists(migrated, "documents", "no_such_column") is False


async def test_tables_exist_rejects_a_non_identifier() -> None:
    from corpus_kb.storage.schema_probe import tables_exist

    with pytest.raises(Exception, match="identifier"):
        await tables_exist(cast("Any", _FakePool({})), "documents; drop")


# --- delete-cascade planner guards (pure functions) ----------------------------


_EDGES: list[Edge] = [
    ("chunks", "doc_id", "documents", "doc_id"),
    ("chunks_vectors", "chunk_id", "chunks", "chunk_id"),
    ("shadow_chunks", "doc_id", "documents", "doc_id"),
    ("shadow_chunks", "parent_id", "shadow_chunks", "shadow_chunk_id"),
    ("relations", "chunk_id", "chunks", "chunk_id"),
]


def test_doomed_tables_orders_children_first_and_skips_set_null() -> None:
    order = doomed_tables(_EDGES)
    # relations survives (SET NULL edge); the child (chunks_vectors)
    # deletes BEFORE its parent (chunks) — children first.
    assert "relations" not in order
    assert order.index("chunks_vectors") < order.index("chunks")
    assert "shadow_chunks" in order


def test_doomed_pred_skips_survivor_parents() -> None:
    doomed = set(doomed_tables(_EDGES))
    edges: list[Edge] = [
        ("chunk_predictions", "chunk_id", "chunks", "chunk_id"),
        ("chunk_predictions", "set_id", "label_sets", "set_id"),
        ("chunks", "doc_id", "documents", "doc_id"),
    ]
    pred = doomed_pred("chunk_predictions", edges, doomed)
    assert "label_sets" not in pred
    assert "chunks" in pred


def test_doomed_pred_refuses_an_unlinked_table() -> None:
    with pytest.raises(InternalError, match="no modeled link"):
        doomed_pred("label_sets", [("chunk_predictions", "set_id", "label_sets", "set_id")], set())


def test_doomed_pred_depth_cap_turns_a_cycle_into_a_typed_error() -> None:
    # a and b reference each other as doomed parents; the recursion can
    # never bottom out at documents, so the depth cap fires.
    edges: list[Edge] = [("a", "pid", "b", "id"), ("b", "pid", "a", "id")]
    with pytest.raises(InternalError, match="table graph"):
        doomed_pred("a", edges, {"a", "b"})


def test_self_reference_returns_pk_then_link() -> None:
    ref = self_reference(_EDGES, "shadow_chunks")
    assert ref == ("shadow_chunk_id", "parent_id")
    assert self_reference(_EDGES, "chunks") is None


def test_set_null_sql_binds_the_parent_anchor() -> None:
    sql = set_null_sql("entities", "source_document_id", "documents", "doc_id", _EDGES)
    assert 'UPDATE "entities" SET "source_document_id" = NULL' in sql
    assert "doc_id = $1 AND tenant_id = $2" in sql


# --- cascade cycle guard (real file) -------------------------------------------


async def test_cascade_refuses_a_parent_cycle_typed(migrated: Any) -> None:
    """A doomed parent cycle raises typed instead of looping.

    An FK-enforced engine cannot even hold such rows (the leaf inserts
    fail), so the guard is driven through a stub gateway whose fetch
    answers a cyclic parent map and an empty anchor query.
    """

    class _StubGateway:
        async def fetch(self, sql: str, *args: object) -> list[dict[str, str]]:
            if "AS parent" in sql:
                # The pk+parent map: each doomed row references the other.
                return [{"pk": "r1", "parent": "r2"}, {"pk": "r2", "parent": "r1"}]
            return [{"pk": "r1"}]  # the anchor query

        def transaction(self, tenant_id: str):
            del tenant_id
            return self

        async def __aenter__(self) -> _StubGateway:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def execute(self, sql: str, *args: object) -> str:
            return "DELETE 0"

    from unittest.mock import patch

    from corpus_kb.storage import duckdb_cascade

    async def _cyclic_descendants(*args: object, **kwargs: object) -> list[str]:
        return ["r1", "r2"]  # the id set: each references the other

    with (
        patch.object(duckdb_cascade, "_descendants", _cyclic_descendants),
        pytest.raises(InternalError, match=r"Cycle in shadow_chunks\.parent_id"),
    ):
        await duckdb_cascade._delete_self_referencing(
            cast("Any", _StubGateway()),
            TENANT,
            str(uuid4()),
            "shadow_chunks",
            ("shadow_chunk_id", "parent_id"),
            "doc_id = $1 AND tenant_id = $2",
        )


# --- DuckDbGraphStore provenance writes -----------------------------------------


async def test_graph_store_provenance_writes(migrated: Any) -> None:
    from corpus_kb.storage.duckdb_store import DuckDbGraphStore
    from corpus_kb.utils.models import Chunk, Document

    store = DuckDbGraphStore(migrated.duplicate_connection(), tenant_id=TENANT)
    doc = Document(
        path="provenance-seed",
        content="",
        source_type="text",
        chunk_count=1,
        size_bytes=10,
    )
    doc_id = await store.add_document(doc)
    assert doc_id == doc.document_id
    # The upsert arm: a second write updates in place (same tenant+source).
    doc.chunk_count = 2
    await store.add_document(doc)
    row = await migrated.fetchrow(
        "SELECT chunk_count FROM documents WHERE doc_id::uuid = $1::uuid", doc.document_id
    )
    assert row is not None and int(row["chunk_count"]) == 2

    chunk = Chunk(
        document_id=doc.document_id,
        text="provenance chunk",
        source_type="text",
    )
    chunk_id = await store.add_chunk(chunk)
    assert chunk_id == chunk.chunk_id
    stored = await migrated.fetchval(
        "SELECT count(*) FROM chunks WHERE doc_id::uuid = $1::uuid", doc.document_id
    )
    assert stored == 1


# --- doctor DuckDB branch --------------------------------------------------------


async def test_doctor_snapshot_marks_postgres_not_used_on_duckdb(monkeypatch: Any) -> None:
    from corpus_kb.tools.doctor_tools import doctor_snapshot

    cfg = {
        "database": {
            "backend": "duckdb",
            "connection_string": "duckdb:///C:/nonexistent-330/doctor.duckdb",
        },
        "embedding": {"provider": "ollama"},
    }
    snapshot = await doctor_snapshot(cfg)
    result = snapshot["result"]
    assert result["postgres"]["reachable"] is False
    assert "not used" in result["postgres"]["message"]
    research = result["research_checks"]
    assert len(research) == 1
    assert research[0]["status"] == "info"
    assert "DuckDB" in research[0]["name"]


# --- lazy handler builds (tools fall back to the given store) ---------------------


async def test_graph_read_tools_build_the_handler_over_the_store(migrated: Any) -> None:
    from corpus_kb.handlers.graph_handler import reset_graph_handler
    from corpus_kb.tools.graph_read_tools import get_entity_relations, search_graph

    reset_graph_handler()
    cfg: dict[str, object] = {"database": {"backend": "duckdb"}, "graph": {"backend": "duckdb"}}
    found = await search_graph(migrated, "nothing-matches", config=cfg)
    assert found["status"] == "success"
    rels = await get_entity_relations(migrated, str(uuid4()), config=cfg)
    assert rels["status"] == "success"


async def test_resolve_gateway_passes_a_gateway_through(migrated: Any) -> None:
    from corpus_kb.projections._gateway import resolve_gateway

    assert resolve_gateway(migrated, None) is migrated


async def test_embed_text_awaits_an_async_embedder(migrated: Any) -> None:
    from corpus_kb.storage.duckdb_rag import DuckDbRagBackend, _embed_text

    class _AsyncEmbedder:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def embed(self, text: str) -> list[float]:
            self.seen.append(text)
            return [0.5, 0.5]

    embedder = _AsyncEmbedder()
    vector = await _embed_text(embedder, "parity")
    assert embedder.seen == ["parity"]
    assert vector == [0.5, 0.5]
    _ = DuckDbRagBackend  # the backend consumes _embed_text through initialize


# --- ingest delete tool arms -------------------------------------------------------


async def test_delete_document_reports_an_error_when_the_pipeline_fails(
    migrated: Any,
) -> None:
    from unittest.mock import patch

    from corpus_kb.tools.ingest_tools import delete_document

    with patch(
        "corpus_kb.storage.duckdb_cascade.delete_document_cascade",
        side_effect=RuntimeError("boom"),
    ):
        result = await delete_document(str(uuid4()), migrated, tenant_id=TENANT)
    assert result["status"] == "error"
    assert "Could not delete" in str(result["message"])


# --- analysis dates arm -------------------------------------------------------------


async def test_document_dates_present_answers_true_after_027(migrated: Any) -> None:
    from corpus_kb.analysis.dates import document_dates_present

    # The migrated mirror carries the 027 column, so the engine probe
    # answers True (the False arm is covered by the schema probes above).
    assert await document_dates_present(migrated) is True
