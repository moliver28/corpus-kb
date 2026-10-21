"""Hermetic command/event-legs E2E on DuckDB (storage portability, sp-command-port).

One tmp ``.duckdb`` file through the :class:`DuckDbGateway`: create →
update → version bump → tag → idempotent replay → delete. The event store
is the ``eventsourcing.sqlite`` backend on a SEPARATE file (the events
store cannot share the analytics file — SQLite cannot open a file DuckDB
holds). Routing rides the gateway through ``VersioningHandler``; parent
expansion rides the gateway reader over ``shadow_chunks``.

No Postgres, no Ollama: the embedder is a stub, the queries are bounded.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.domain.aggregates import Document
from corpus_kb.domain.application import get_app
from corpus_kb.domain.models import AddEntityCommand, RoutedQuery
from corpus_kb.handlers.command_handler import CommandHandler
from corpus_kb.handlers.idempotency import IdempotencyChecker
from corpus_kb.handlers.router_handler import RouterHandler
from corpus_kb.handlers.tag_handler import TagHandler
from corpus_kb.handlers.versioning_handler import VersioningHandler
from corpus_kb.rag.parent_expand import expand_to_parent
from corpus_kb.storage.duckdb_gateway import DuckDbGateway
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string

TENANT = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
async def gateway(tmp_path: Path):
    db = tmp_path / "cmd.duckdb"
    run_duckdb_migrations(duckdb_path_to_conn_string(str(db)))
    gw = await DuckDbGateway.connect(duckdb_path_to_conn_string(str(db)))
    yield gw
    await gw.close()


@pytest.fixture
def event_store_path(tmp_path: Path) -> str:
    return str(tmp_path / "events.store")


@pytest.fixture
async def command_handler(gateway: DuckDbGateway, event_store_path: str) -> CommandHandler:
    """CommandHandler wired for the duckdb event store (config-truth switch).

    The analytics gateway rides the constructor: the delete leg's row
    store on DuckDB (no asyncpg pool exists there).
    """
    return CommandHandler(
        {"database": {"backend": "duckdb", "connection_string": event_store_path}},
        pool=None,
        gateway=gateway,
    )


@pytest.fixture(autouse=True)
def _close_event_apps():
    from corpus_kb.domain.application import reset_app

    yield
    reset_app()


async def _insert_document(gw: DuckDbGateway, doc_id: UUID) -> None:
    await gw.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type) VALUES (?, ?, ?, ?)",
        str(doc_id),
        str(TENANT),
        "e2e.md",
        "markdown",
    )


async def test_command_e2e_legs_on_duckdb(
    gateway: DuckDbGateway,
    command_handler: CommandHandler,
    event_store_path: str,
) -> None:
    # 1 CREATE: an entity command persists through the duckdb event store.
    created = command_handler.handle_add_entity(
        AddEntityCommand(tenant_id=TENANT, name="UserService", entity_type="class")
    )
    assert created["status"] == "success"
    assert set(created) == {"status", "entity_id", "name", "entity_type"}
    UUID(created["entity_id"])  # parses
    assert command_handler.app is get_app(event_store_path, backend="duckdb")

    # 2 UPDATE + 3 VERSION BUMP: the second ChunksAdded save bumps version.
    app = command_handler.app
    doc = Document(tenant_id=TENANT, source="e2e.md", source_type="markdown")
    doc.add_chunks(2, [])
    app.save(doc)
    version_after_create = doc.version
    doc.add_chunks(3, [])
    app.save(doc)
    assert doc.version > version_after_create
    reloaded = app.repository.get(doc.id)
    assert reloaded.version == doc.version
    assert reloaded.chunk_count == 5

    # 4 TAG: create, dedupe, link to a projection row, read back ordered.
    await _insert_document(gateway, doc.id)
    tags = TagHandler(gateway)
    first = await tags.handle_add_tag(TENANT, "zeta", color="#112233")
    assert set(first) == {"tag_id", "name"}
    duplicate = await tags.handle_add_tag(TENANT, "zeta", color="#ffffff")
    assert duplicate == {"name": "zeta", "status": "already_exists"}
    for name in ("zeta", "alpha"):
        tagged = await tags.handle_tag_document(TENANT, doc.id, name)
        assert tagged == {"status": "success", "doc_id": str(doc.id), "tag": name}
    rows = await tags.handle_get_document_tags(TENANT, doc.id)
    assert [(r["name"], r["color"]) for r in rows] == [("alpha", None), ("zeta", "#112233")]

    # 5 IDEMPOTENT REPLAY: same key twice → one effect, first result wins.
    checker = IdempotencyChecker(gateway)
    command_id = uuid4()
    assert await checker.check(TENANT, command_id) is None
    payload = {"text": "hello", "source": "e2e"}
    recorded = {"status": "success", "chunk_count": 7}
    await checker.record(TENANT, command_id, "IngestTextCommand", payload, recorded)
    await checker.record(TENANT, command_id, "IngestTextCommand", payload, {"chunk_count": 99})
    replayed = await checker.check(TENANT, command_id)
    assert replayed is not None
    assert set(replayed) == {"command_type", "command_payload", "result", "created_at"}
    assert replayed["command_type"] == "IngestTextCommand"
    stored = await gateway.fetchval(
        "SELECT result FROM idempotency_keys WHERE idempotency_key = ?", str(command_id)
    )
    assert "7" in str(stored) and "99" not in str(stored)
    assert (
        await gateway.fetchval(
            "SELECT COUNT(*) FROM idempotency_keys WHERE idempotency_key = ?",
            str(command_id),
        )
        == 1
    )

    # 6 DELETE: children first (DuckDB FKs have no cascade), then the
    # command leg — the shared tool over the analytics gateway, audited
    # into the duckdb event store.
    from corpus_kb.domain.models import DeleteDocumentCommand

    await gateway.execute("DELETE FROM document_tags WHERE doc_id = ?", str(doc.id))
    deleted = await command_handler.handle_delete_document(
        DeleteDocumentCommand(doc_id=doc.id, tenant_id=TENANT)
    )
    assert deleted["status"] == "success"
    assert deleted["aggregate_id"] == str(doc.id)  # the audit event saved
    assert deleted["aggregate_version"] > version_after_create
    assert (
        await gateway.fetchval("SELECT COUNT(*) FROM documents WHERE doc_id = ?", str(doc.id)) == 0
    )
    unknown = await command_handler.handle_delete_document(
        DeleteDocumentCommand(doc_id=uuid4(), tenant_id=TENANT)
    )
    assert unknown["status"] == "success"  # unknown id stays idempotent
    assert "aggregate_id" not in unknown  # no stored history: no audit keys
    assert await tags.handle_get_document_tags(TENANT, doc.id) == []


async def test_versioning_stats_on_duckdb(gateway: DuckDbGateway) -> None:
    await _insert_document(gateway, uuid4())
    versioning = VersioningHandler(gateway)
    stats = await versioning.handle_get_stats(TENANT)
    assert stats["documents"] == 1
    assert stats["chunks"] == 0
    assert stats["total_events"] == 0  # event_store table is PG-shaped; absent here
    doc_stats = await versioning.handle_query_document_stats(TENANT)
    assert doc_stats["total_documents"] == 1
    assert doc_stats["by_source_type"] == [{"source_type": "markdown", "count": 1}]
    assert await versioning.handle_list_versions(TENANT) == []
    tables = await versioning.handle_sql_tables(TENANT)
    names = [row["name"] for row in tables]
    assert names == sorted(names)
    assert "documents" in names and "chunks" in names
    assert all(row["owner"] == "duckdb" for row in tables)


class _StubEmbedder:
    """Two orthogonal prototype directions, picked by keyword."""

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if "count" in text else [0.0, 1.0]


class _StubQueryHandler:
    async def handle_search(self, query):
        return []


class _StubGraphHandler:
    async def handle_search_graph(self, *args, **kwargs):
        return []


async def test_router_relational_leg_on_duckdb(gateway: DuckDbGateway) -> None:
    versioning = VersioningHandler(gateway)
    router = RouterHandler(
        _StubQueryHandler(),
        _StubGraphHandler(),
        versioning,
        _StubEmbedder(),
        {
            "routing": {
                "confidence_threshold": 0.35,
                "prototypes": {
                    "relational": ["count documents"],
                    "semantic": ["find the text about"],
                },
            }
        },
    )
    result = await router.handle_routed_query(
        RoutedQuery(tenant_id=TENANT, query="count documents in the store", k=5)
    )
    assert result.decision.route == "relational"
    assert result.decision.fallback is False
    assert result.results[0]["total_documents"] == 0


async def test_parent_expand_on_duckdb(gateway: DuckDbGateway) -> None:
    from corpus_kb.domain.models import SearchResult

    doc_id = uuid4()
    await _insert_document(gateway, doc_id)
    hit = uuid4()
    await gateway.execute(
        "INSERT INTO shadow_chunks (shadow_chunk_id, tenant_id, doc_id, level, seq,"
        " text, token_count) VALUES (?, ?, ?, 'parent', 1, 'BIG PARENT CONTEXT', 100)",
        str(uuid4()),
        str(TENANT),
        str(doc_id),
    )
    parent_id = await gateway.fetchval(
        "SELECT shadow_chunk_id FROM shadow_chunks WHERE tenant_id = ? AND level = 'parent'",
        str(TENANT),
    )
    await gateway.execute(
        "INSERT INTO shadow_chunks (shadow_chunk_id, tenant_id, doc_id, level, seq,"
        " parent_id, text, token_count) VALUES (?, ?, ?, 'chunk', 1, ?, 'mid level', 50)",
        str(uuid4()),
        str(TENANT),
        str(doc_id),
        str(parent_id),
    )
    chunk_id = await gateway.fetchval(
        "SELECT shadow_chunk_id FROM shadow_chunks WHERE tenant_id = ? AND level = 'chunk'",
        str(TENANT),
    )
    await gateway.execute(
        "INSERT INTO shadow_chunks (shadow_chunk_id, tenant_id, doc_id, level, seq,"
        " parent_id, text, token_count, metadata)"
        " VALUES (?, ?, ?, 'child', 1, ?, 'small child', 10, ?)",
        str(uuid4()),
        str(TENANT),
        str(doc_id),
        str(chunk_id),
        json.dumps({"v1_chunk_id": str(hit)}),
    )
    results = [
        SearchResult(chunk_id=hit, text="small child", score=0.9, source="e2e.md", doc_id=doc_id)
    ]
    expanded = await expand_to_parent(gateway, str(TENANT), results)
    assert [str(r.chunk_id) for r in expanded] == [str(hit)]
    assert expanded[0].text == "BIG PARENT CONTEXT"
    assert expanded[0].score == 0.9
