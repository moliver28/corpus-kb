"""Backend operation parity: every offline catalogue op runs on DuckDB.

Issue #330 Part 1: a study on ``database.backend: duckdb`` must run the
same operations a Postgres study runs, with the same envelope. This
module drives the SHARED leg of each listed operation from
``test_surface_parity_deep`` (the same function the HTTP/socket/MCP
surfaces route through) against ONE migrated DuckDB file, and collects
every failure instead of stopping at the first — the report names all
broken operations at once.

A leg passes when it returns its envelope without raising an unexpected
error: no typed "pending their port" refusal, no engine-shaped
exception. Empty result sets are the honest outcome for an empty store.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from test_surface_parity_deep import DOC_ID, LEGS, TENANT

from tests.live_embed import live_embed_dimensions, live_embed_model

ParityLegs = tuple[str, ...]

#: The 27 operations issue #330 names for the DuckDB parity proof,
#: the three Track C project-table ops, and the issue #350 reads.
PARITY_OPS: ParityLegs = (
    "topics_list",
    "labeling_list",
    "labeling_lfs",
    "classification_list",
    "analysis_select_method",
    # "analysis_route" in the issue names the routed-query operation (the
    # catalogue has no analysis_route row; the query leg IS the router).
    "query",
    "statements_list",
    "statements_timeline",
    "conflicts_list",
    "beliefs_current",
    "queue_enqueue",
    "queue_status",
    "ingest_status",
    "list_documents",
    "get_document_tags",
    "get_metadata",
    "add_entity",
    "add_relation",
    "search_graph",
    "get_entity_relations",
    "graph_neighbors",
    "graph_traverse",
    "release_assess",
    "notebook_evidence",
    "notebook_uncoded",
    "notebook_overlap",
    "delete_document",
    # Track C: the per-project raw-table ops (provision before drop).
    "project_provision",
    "project_list",
    "project_drop",
    # Issue #350: the coding reads + the event-store health read (the
    # status/export SQL variants and the events sidecar resolution are
    # exactly what the duckdb backend must answer).
    "coding_status",
    "coding_export",
    "events_summary",
)


@pytest.fixture()
async def parity_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The migrated DuckDB gateway behind a REAL startup_duckdb boot.

    The server's handler singletons (command, query, router, graph, tag,
    versioning, coding) are wired exactly as a ``database.backend:
    duckdb`` server wires them — the legs then run the way a live study
    runs, never against an unwired singleton.
    """
    import yaml

    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.handlers import command_handler as command_module
    from corpus_kb.handlers import (
        graph_handler,
        idempotency,
        router_handler,
        tag_handler,
        versioning_handler,
    )
    from corpus_kb.server_wiring_duckdb import startup_duckdb
    from corpus_kb.storage.gateway import DuckDbGateway

    # A previous test's wiring must not leak into this boot.
    command_module.reset_command_handler()
    idempotency.reset_idempotency_checker()
    graph_handler.reset_graph_handler()
    tag_handler.reset_tag_handler()
    versioning_handler.reset_versioning_handler()
    router_handler.reset_router_handler()
    from corpus_kb.handlers.query_wiring import reset_query_handler

    reset_query_handler()

    db_path = tmp_path / "parity.duckdb"
    dsn = f"duckdb:///{db_path.as_posix()}"
    run_duckdb_migrations(dsn)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "database:\n"
        "  backend: duckdb\n"
        f"  connection_string: {db_path.as_posix()}\n"
        "graph:\n"
        "  backend: duckdb\n"
        "extraction:\n"
        "  backend: regex\n"
        # Name the model this leg actually pulled (zero-drift F3
        # follow-up); a stale hardcoded name 404s here. Dimensions
        # travel with the model so the embedder's width validation
        # passes, and a width-mismatched vector arm fails open into the
        # degraded lexical arm (the search contract).
        "embedding:\n"
        "  provider: ollama\n"
        f"  model: {live_embed_model()}\n"
        f"  dimensions: {live_embed_dimensions()}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CORPUS_KB_CONFIG", str(cfg_path))
    # Pin the env DSN too: it overrides the file's connection_string, and
    # a leaked CORPUS_KB_DATABASE_URL from another fixture would point the
    # boot at the wrong database.
    monkeypatch.setenv("CORPUS_KB_DATABASE_URL", dsn)

    cfg = cast("dict[str, Any]", yaml.safe_load(cfg_path.read_text(encoding="utf-8")))
    services = await startup_duckdb(cfg, dsn)
    gateway = services["gateway"]
    assert isinstance(gateway, DuckDbGateway)
    yield gateway
    await gateway.close()


async def _seed_document(gateway: Any, tenant: str, doc_id: str) -> None:
    """One document row (plus a chunk) so the doc-keyed legs have an anchor."""
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ($1::uuid, $2, $3, $4)",
        doc_id,
        tenant,
        "parity-seed",
        "text",
    )
    await gateway.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type)"
        " VALUES ($1::uuid, $2, $3::uuid, 0, $4, $5)",
        str(uuid4()),
        tenant,
        doc_id,
        "parity seed text",
        "text",
    )


@pytest.mark.asyncio
async def test_all_parity_operations_run_on_duckdb(
    parity_store: Any, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the 30 shared legs on one migrated file; collect every failure.

    ``queue_enqueue`` books a real row (its content hash names a tmp file,
    so a second run would deduplicate), and ``delete_document`` runs LAST
    on the seeded document — its cascade is one of the operations under
    proof.
    """
    book = tmp_path / "queue-book.md"
    book.write_text("queue book body", encoding="utf-8")
    ctx: dict[str, Any] = {
        "pool": parity_store,
        "config": {},
        _file_path_key(): str(book),
    }
    await _seed_document(parity_store, TENANT, DOC_ID)

    failures: list[str] = []
    for op in PARITY_OPS:
        leg = LEGS.get(op)
        if leg is None:
            failures.append(f"{op}: no shared leg in test_surface_parity_deep")
            continue
        try:
            envelope = await leg.shared(ctx)
        except Exception as exc:  # collected, never first-stop
            failures.append(f"{op}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(envelope, dict) or "status" not in envelope:
            failures.append(f"{op}: envelope without a status key: {type(envelope).__name__}")
    assert not failures, "DuckDB operation parity failures:\n" + "\n".join(failures)

    # The delete-document leg really deleted the seeded document: its
    # cascade (chunks, tags, vectors) left no orphan behind.
    left = await parity_store.fetchval(
        "SELECT count(*) FROM documents WHERE doc_id::uuid = $1::uuid", DOC_ID
    )
    assert left == 0
    orphan_chunks = await parity_store.fetchval(
        "SELECT count(*) FROM chunks WHERE doc_id::uuid = $1::uuid", DOC_ID
    )
    assert orphan_chunks == 0


def _file_path_key() -> str:
    """The deep module's ctx key for a file path (kept local: it is a test
    layout constant, not an API)."""
    return "file_path"


def test_parity_op_list_is_exact() -> None:
    """Every named operation exists as a shared leg (catalogue drift pin)."""
    missing = [op for op in PARITY_OPS if op not in LEGS]
    assert not missing, f"legs missing for: {missing}"
