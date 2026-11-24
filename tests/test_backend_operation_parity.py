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

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, cast
from uuid import uuid4

import pytest
from test_surface_parity_deep import _QUEUE_ID, DOC_ID, LEGS, TENANT

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
    # Novice-UX W2: every engine-neutral analysis op that ran through the
    # dual-engine seam (tenant_connection) without a DuckDB leg. The rule
    # for this block: an op joins when its store path runs on EVERY
    # outcome in this gate world; honest guard envelopes (disabled,
    # empty, unavailable) count, engine-shaped exceptions do not. The
    # NLI pair joins under the same rule — the store read (the vector
    # prefilter) runs on every outcome, and its chat arm is
    # engine-independent, degrading to the honest unavailable envelope
    # on a model-less host. analysis_route stays out: it reads no store
    # on any path (the deep surface gate covers its envelope).
    "keyphrases_extract",
    "lexical_classify",
    "neighbors_classify",
    "centroids_classify",
    "clusters_inspect",
    "duplicates_screen",
    "outliers_rank",
    "sentiment_screen",
    "active_learn_next",
    "nli_classify",
    "contradictions_screen",
    # W3 UXR methods: the survey read runs through the same chunk-read
    # seam the sentiment leg uses; severity is lexical-only; emotion
    # rides the NLI pair's seams (store read + honest unavailable).
    "survey_openend_codes",
    "usability_severity",
    "emotion_tag",
    "labeling_build",
    "classification_build",
    "topics_build",
    "topics_trends",
    "analysis_backfill_dates",
    "analysis_embed_meta",
    "analyze_run",
    # W3 parity-test-legs wave: the audited-missing DuckDB legs. These run
    # their shared legs with only ctx additions (a gold file, one seeded
    # queue row). The seed-dependent ops — conflicts_resolve and the
    # beliefs trio — pin in the seeded test below: their shared legs seed
    # through raw asyncpg sessions (pool.acquire + set_config), which is
    # Postgres-shaped, so here the same rows land through gateway.execute
    # and the SAME product functions the shared legs call run unmodified.
    "search_context",
    "verify_answer",
    "notebook_ask",
    "conflicts_scan",
    "labeling_triage",
    "labeling_gold_check",
    "queue_cancel",
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
        query_handler,
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
    monkeypatch.setattr(query_handler, "_query_handler", None)

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
    so a second run would deduplicate), ``queue_cancel`` cancels the
    seeded pending row under the catalogue's fixed queue id, and
    ``delete_document`` runs LAST on the seeded document — its cascade is
    one of the operations under proof.
    """
    book = tmp_path / "queue-book.md"
    book.write_text("queue book body", encoding="utf-8")
    gold = tmp_path / "parity-gold.json"
    gold.write_text(json.dumps({"chunk-1": "reports"}), encoding="utf-8")
    ctx: dict[str, Any] = {
        "pool": parity_store,
        "config": {},
        _file_path_key(): str(book),
        "gold_path": str(gold),
    }
    await _seed_document(parity_store, TENANT, DOC_ID)
    # The cancel leg needs a pending row under the catalogue's fixed id;
    # its content hash stays away from the enqueue leg's own row.
    await parity_store.execute(
        "INSERT INTO ingest_queue (id, tenant_id, path, content_hash)"
        " VALUES ($1::uuid, $2, $3, $4)",
        _QUEUE_ID,
        TENANT,
        str(book) + "-cancel-seed",
        "parity-cancel-seed-content-hash",
    )

    failures: list[str] = []
    # The three analysis date ops run HOT here: the dormant default (every
    # analysis flag off) would mask the date path behind a "disabled"
    # envelope, so these legs unmask it with the over_time flag on. The
    # backfill answers its honest engine envelope (the DuckDB documents
    # row is immutable once chunks reference it); the trends leg reaches
    # its store read and answers on no data.
    hot_config: dict[str, Any] = {
        "analysis": {"topics": {"over_time": {"enabled": True}}},
    }
    hot_ops = {"topics_trends", "analysis_backfill_dates", "analyze_run"}
    for op in PARITY_OPS:
        leg = LEGS.get(op)
        if leg is None:
            failures.append(f"{op}: no shared leg in test_surface_parity_deep")
            continue
        try:
            envelope = await leg.shared({**ctx, "config": hot_config} if op in hot_ops else ctx)
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


# -- Seed-dependent legs -----------------------------------------------------
#
# The deep module's shared legs for conflicts_resolve and the beliefs trio
# seed their world through raw asyncpg sessions (``pool.acquire`` plus the
# ``set_config`` tenant GUC). The DuckDB gateway has no ``acquire`` and no
# GUC, so this module lands the same rows through ``gateway.execute`` and
# then calls the SAME product functions those shared legs call, with the
# same arguments. Nothing else differs from the shared legs above.


async def _seed_anchor(gateway: Any, tenant: str) -> str:
    """Seed the one shared doc + chunk anchor; return the chunk id.

    Both seeded worlds hang their statements on this anchor, so the
    insert carries ``ON CONFLICT DO NOTHING`` and a second call is a
    no-op that still answers the chunk id.
    """
    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ($1::uuid, $2, $3, $4) ON CONFLICT DO NOTHING",
        DOC_ID,
        tenant,
        "parity-seed",
        "text",
    )
    await gateway.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type)"
        " VALUES ($1::uuid, $2, $3::uuid, 0, $4, $5) ON CONFLICT DO NOTHING",
        str(uuid4()),
        tenant,
        DOC_ID,
        "parity seed text",
        "text",
    )
    chunk_id = await gateway.fetchval(
        "SELECT chunk_id FROM chunks WHERE doc_id::uuid = $1::uuid AND chunk_index = 0",
        DOC_ID,
    )
    return str(chunk_id)


async def _seed_conflict_world(gateway: Any, tenant: str, chunk_id: str) -> str:
    """Seed two opposite statements and one flag; return the conflict id.

    Mirrors the deep module's conflict seed row for row, minus the
    ``ON CONFLICT`` clauses: each parity test owns a fresh DuckDB file.
    """
    seed_texts = ("I like ice cream.", "I dislike ice cream now.")
    for seed_text in seed_texts:
        await gateway.execute(
            "INSERT INTO statements (tenant_id, chunk_id, document_id, statement_hash,"
            " text, template_id, template_version, entities, metadata)"
            " VALUES ($1, $2::uuid, $3::uuid, $4, $5, 'preference', 'v1',"
            " '[]'::jsonb, '{}'::jsonb)",
            tenant,
            str(chunk_id),
            DOC_ID,
            sha256(seed_text.encode("utf-8")).hexdigest(),
            seed_text,
        )
    stmt_a = await gateway.fetchval(
        "SELECT statement_id FROM statements WHERE tenant_id = $1 AND text = $2",
        tenant,
        seed_texts[0],
    )
    stmt_b = await gateway.fetchval(
        "SELECT statement_id FROM statements WHERE tenant_id = $1 AND text = $2",
        tenant,
        seed_texts[1],
    )
    await gateway.execute(
        "INSERT INTO statement_conflicts (tenant_id, statement_a_id, statement_b_id,"
        " anchor_name, kind, gate_reached, verdict, metadata)"
        " VALUES ($1, $2::uuid, $3::uuid, 'ice cream', 'relation_swap', 'B', 'conflict',"
        " '{}'::jsonb)",
        tenant,
        str(stmt_a),
        str(stmt_b),
    )
    conflict_id = await gateway.fetchval(
        "SELECT conflict_id FROM statement_conflicts"
        " WHERE tenant_id = $1 AND anchor_name = 'ice cream'"
        " ORDER BY created_at DESC, conflict_id ASC LIMIT 1",
        tenant,
    )
    return str(conflict_id)


async def _seed_belief_world(gateway: Any, tenant: str, chunk_id: str) -> dict[str, str]:
    """Seed a like-then-dislike arc ten years apart; rebuild beliefs.

    Returns the belief triple the timeline, predict, and compare legs
    read. Mirrors the deep module's belief seed; the old statement carries
    an ``observed_at`` ten years back so two eras segment cleanly.
    """
    from corpus_kb.beliefs.store import BeliefStore

    now = datetime.now(UTC)
    rows = [
        (
            sha256(b"belief-seed-old").hexdigest(),
            "I like ice cream.",
            now - timedelta(days=3650),
        ),
        (
            sha256(b"belief-seed-new").hexdigest(),
            "I dislike ice cream now.",
            now,
        ),
    ]
    for seed_hash, seed_text, observed in rows:
        await gateway.execute(
            "INSERT INTO statements (tenant_id, chunk_id, document_id, statement_hash,"
            " text, template_id, template_version, entities, exchange_id, confidence,"
            " observed_at, metadata)"
            " VALUES ($1, $2::uuid, $3::uuid, $4, $5, 'preference', 'v1', $6::jsonb, 777, 0.9,"
            " $7, '{}'::jsonb)",
            tenant,
            str(chunk_id),
            DOC_ID,
            seed_hash,
            seed_text,
            json.dumps([{"name": "ice cream", "entity_type": "CONCEPT"}]),
            observed,
        )
    store = BeliefStore(gateway, tenant)
    await store.rebuild()
    return {"subject": "speaker:777", "relation": "preference", "object": "ice cream"}


@pytest.mark.asyncio
async def test_seeded_parity_operations_run_on_duckdb(parity_store: Any) -> None:
    """Run conflicts_resolve and the beliefs trio on gateway-shaped seeds.

    Every leg must answer its envelope without an engine-shaped error,
    and the resolution must stick: the flag row reads back dismissed.
    """
    from corpus_kb.tools.beliefs_tools import beliefs_compare, beliefs_predict, beliefs_timeline
    from corpus_kb.tools.conflicts_tools import conflicts_resolve

    failures: list[str] = []

    chunk_id = await _seed_anchor(parity_store, TENANT)
    conflict_id = await _seed_conflict_world(parity_store, TENANT, chunk_id)
    try:
        resolved = await conflicts_resolve(
            parity_store,
            config={},
            conflict_id=conflict_id,
            resolved_verdict="dismissed",
            resolved_by="parity-leg",
        )
        if resolved.get("status") != "success":
            failures.append(f"conflicts_resolve: {resolved}")
        verdict = await parity_store.fetchval(
            "SELECT resolved_verdict FROM statement_conflicts WHERE conflict_id::uuid = $1::uuid",
            conflict_id,
        )
        if verdict != "dismissed":
            failures.append(f"conflicts_resolve: row reads back {verdict!r}, not 'dismissed'")
    except Exception as exc:
        failures.append(f"conflicts_resolve: {type(exc).__name__}: {exc}")

    triple = await _seed_belief_world(parity_store, TENANT, chunk_id)
    belief_legs: dict[str, Any] = {
        "beliefs_timeline": lambda: beliefs_timeline(parity_store, config={}, **triple),
        "beliefs_predict": lambda: beliefs_predict(parity_store, config={}, **triple),
        "beliefs_compare": lambda: beliefs_compare(
            parity_store, config={}, subject=triple["subject"]
        ),
    }
    for op, run in belief_legs.items():
        try:
            envelope = await run()
        except Exception as exc:
            failures.append(f"{op}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(envelope, dict) or envelope.get("status") != "success":
            failures.append(f"{op}: {envelope}")

    assert not failures, "DuckDB seeded-operation parity failures:\n" + "\n".join(failures)
