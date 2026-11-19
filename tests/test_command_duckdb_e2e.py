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

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.domain.aggregates import Document
from corpus_kb.domain.application import get_app
from corpus_kb.domain.models import AddEntityCommand, RoutedQuery
from corpus_kb.handlers.coding_handler import CodingHandler
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
    doc.add_chunks(TENANT, 2, [])
    app.save(doc)
    version_after_create = doc.version
    doc.add_chunks(TENANT, 3, [])
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


# ============================================================================
# W3: coding command-replay legs on DuckDB
#
# One leg per registered coding command, driven through the ONE shared
# tool function every surface routes through, on the migrated gateway
# file. The two transport boundaries that need a model (the codebook
# embedder and the chunk coder) answer through deterministic doubles
# patched at the module seams; every store write, read, and event stays
# real. Replay pins: the codebook load is content-hash idempotent (the
# second load answers the SAME version id) and the re-dispatched cell is
# a duplicate no-op (append-only table, nothing written twice).
# ============================================================================

_EMBED_DIM = 2560

_CODEBOOK: dict[str, Any] = {
    "paradigm": "codebook",
    "categories": [
        {
            "id": "C1",
            "name": "trust",
            "brief_definition": "trust in management",
            "inclusion_criteria": "confidence statements",
            "exclusion_criteria": "e-trust",
            "examples": [],
            "theory": {},
        },
        {
            "id": "C2",
            "name": "fear",
            "brief_definition": "fear of layoffs",
            "inclusion_criteria": "i-fear",
            "exclusion_criteria": "e-fear",
            "examples": [],
            "theory": {},
        },
    ],
}


def _one_hot_literal(text: str, dim: int = _EMBED_DIM, instructed: bool = False) -> str:
    """A deterministic one-hot ``FLOAT[dim]`` literal for one text.

    Equal texts embed equal, different texts embed orthogonal, so every
    replay scores identically.
    """
    index = int(hashlib.sha256(f"{instructed}:{text}".encode()).hexdigest(), 16) % dim
    vector = [0.0] * dim
    vector[index] = 1.0
    return "[" + ",".join(format(float(v), ".9g") for v in vector) + "]"


def _coder_reply(decision: str, quote: str) -> dict[str, Any]:
    """One valid coder reply for the canned dispatch double."""
    return {
        "decisions": [
            {
                "code_id": "C1",
                "decision": decision,
                "confidence": 0.9 if decision == "assign" else 0.95,
                "evidence_quote": quote if decision == "assign" else None,
                "rationale": "parity leg",
            }
        ],
        "logprobs": [],
    }


async def _seed_chunk(gateway: DuckDbGateway, text: str) -> UUID:
    """One document + chunk row; returns the chunk id."""
    doc_id = uuid4()
    await _insert_document(gateway, doc_id)
    chunk_id = uuid4()
    await gateway.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, metadata)"
        " VALUES ($1::uuid, $2, $3::uuid, 0, $4, '{}')",
        str(chunk_id),
        str(TENANT),
        str(doc_id),
        text,
    )
    return chunk_id


async def _seed_proposal_world(gateway: DuckDbGateway) -> int:
    """One exchange, one answer unit (both with 1024-d vectors), one proposal.

    The promotion re-derivation reads the member unit's answer view, the
    exchange QA view, and the proposed_code log; all three land here with
    deterministic vector literals.
    """
    doc_id = uuid4()
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type) VALUES (?, ?, ?, ?)",
        str(doc_id),
        str(TENANT),
        f"promote-{doc_id}.md",
        "markdown",
    )
    await gateway.execute(
        "INSERT INTO research_exchanges (exchange_id, tenant_id, doc_id, seq,"
        " q_unit_ids, a_unit_ids, embedding)"
        " VALUES (901, $1, $2::uuid, 0, [], [], $3::FLOAT[1024])",
        str(TENANT),
        str(doc_id),
        _one_hot_literal("qa-view", 1024),
    )
    await gateway.execute(
        "INSERT INTO research_units (unit_id, tenant_id, doc_id, seq, text, text_sha256,"
        " exchange_id, role_in_exchange, embedding)"
        " VALUES (902, $1, $2::uuid, 0, $3, $4, 901, 'answer', $5::FLOAT[1024])",
        str(TENANT),
        str(doc_id),
        "We trust in management here.",
        hashlib.sha256(b"We trust in management here.").hexdigest(),
        _one_hot_literal("answer-view", 1024),
    )
    # The view loader keeps only units whose exchange also carries a
    # question arm, so the world needs one question unit beside the answer.
    await gateway.execute(
        "INSERT INTO research_units (unit_id, tenant_id, doc_id, seq, text, text_sha256,"
        " exchange_id, role_in_exchange, embedding)"
        " VALUES (903, $1, $2::uuid, 1, $3, $4, 901, 'question', $5::FLOAT[1024])",
        str(TENANT),
        str(doc_id),
        "Do you trust management?",
        hashlib.sha256(b"Do you trust management?").hexdigest(),
        _one_hot_literal("question-view", 1024),
    )
    await gateway.execute(
        "INSERT INTO research_proposed_codes (proposed_id, tenant_id, cluster_id, run_id,"
        " member_unit_ids, n_members)"
        " VALUES (7, $1, 'parity-cluster', 'parity-run', [902], 1)",
        str(TENANT),
    )
    return 7


async def test_coding_command_replay_legs_on_duckdb(
    gateway: DuckDbGateway,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All five coding commands run and replay on the DuckDB file.

    The config the research commands resolve internally points the event
    store at this file's SQLite sidecar; the embedder and coder seams
    answer without a model. Nothing here touches Postgres or Ollama.
    """
    from corpus_kb.tools.coding_tools import coding_code_batch, coding_embed_codebook
    from corpus_kb.tools.research_tools import codebook_promote, coding_run, inductive_run

    # The research commands load config at call time; this config binds
    # the event store to the SQLite sidecar of THIS analytics file.
    cfg = {
        "database": {
            "backend": "duckdb",
            "connection_string": f"duckdb:///{(tmp_path / 'cmd.duckdb').as_posix()}",
        }
    }
    monkeypatch.setattr("corpus_kb.config.load_config", lambda: cfg)

    def _stub_embed_fn(self: CodingHandler) -> Any:
        async def embed_fn(text: str, instructed: bool = False) -> str:
            return _one_hot_literal(text)

        return embed_fn

    monkeypatch.setattr(CodingHandler, "_default_embed_fn", _stub_embed_fn)

    async def _canned_call_ollama(window: str, model: str, **kwargs: Any) -> dict[str, Any]:
        return _coder_reply("assign", "trust in management")

    monkeypatch.setattr("corpus_kb.coding.coder_dispatch.call_ollama", _canned_call_ollama)

    # 1 EMBED-CODEBOOK + replay: the second load answers the SAME version
    # id (content-hash idempotence), the registry holds two codes.
    first = await coding_embed_codebook(gateway, _CODEBOOK, config=cfg)
    assert first["status"] == "success"
    version_id = str(first["codebook_version_id"])
    UUID(version_id)  # parses
    codes = await gateway.fetch(
        "SELECT code_id FROM code_registry WHERE tenant_id = $1 ORDER BY code_id",
        str(TENANT),
    )
    assert [str(row["code_id"]) for row in codes] == ["C1", "C2"]

    replay = await coding_embed_codebook(gateway, _CODEBOOK, config=cfg)
    assert replay["status"] == "success"
    assert str(replay["codebook_version_id"]) == version_id

    # 2 CODE-BATCH + replay: one cell lands once; the re-dispatch is a
    # duplicate no-op against the append-only chunk_codes table.
    chunk_id = await _seed_chunk(gateway, "We trust in management here.")
    cells = [{"chunk_id": str(chunk_id), "code_ids": ["C1"]}]
    batch = await coding_code_batch(gateway, cells, coder="stub", model="stub", config=cfg)
    assert batch["status"] == "success"
    assert batch["cells_coded"] == 1
    assert batch["cells_routed"] == 1
    row = await gateway.fetchrow(
        "SELECT decision, route FROM chunk_codes WHERE tenant_id = $1 AND chunk_id = $2::uuid",
        str(TENANT),
        str(chunk_id),
    )
    assert row is not None and row["decision"] == "assign"

    again = await coding_code_batch(gateway, cells, coder="stub", model="stub", config=cfg)
    assert again["status"] == "success"
    assert again["cells_coded"] == 1
    assert again["cells_routed"] == 0
    assert again["results"][0]["duplicates"] == 1
    count = await gateway.fetchval(
        "SELECT count(*) FROM chunk_codes WHERE tenant_id = $1 AND chunk_id = $2::uuid",
        str(TENANT),
        str(chunk_id),
    )
    assert count == 1  # append-only: the replay wrote nothing

    # 3 CODING-RUN: one deductive run over the loaded codebook and an
    # empty research corpus answers the zero-unit summary, twice.
    for _ in range(2):
        summary = await coding_run(gateway, version_id)
        assert summary["status"] == "success"
        assert summary["n_units"] == 0
        UUID(str(summary["run_id"]))  # parses
        assert UUID(str(summary["codebook_version_id"])) == UUID(version_id)

    # 4 CODEBOOK-PROMOTE: one proposal with one member unit mints a NEW
    # codebook version and marks the proposal promoted in the log.
    proposed_id = await _seed_proposal_world(gateway)
    promoted = await codebook_promote(gateway, proposed_id, "trust", "trust in management")
    assert promoted["status"] == "promoted"
    assert promoted["n_members"] == 1
    UUID(str(promoted["codebook_version_id"]))
    assert str(promoted["codebook_version_id"]) != version_id  # a NEW version
    marked = await gateway.fetchval(
        "SELECT status FROM research_proposed_codes WHERE proposed_id = $1",
        proposed_id,
    )
    assert marked == "promoted"

    # 5 INDUCTIVE-RUN (last). No codable units, so the pass answers the
    # honest nothing-to-cluster envelope (or the no-extra unavailable
    # envelope).
    inductive = await inductive_run(gateway)
    assert inductive["status"] in {"success", "unavailable"}
    if inductive["status"] == "success":
        assert inductive["n_units"] == 0
