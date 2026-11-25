"""Op-by-op DuckDB execution matrix — the parity PR-8 live driver (U1/U2).

COMMITTED, RUN OFF-PEAK ONLY: the live run needs a real migrated DuckDB
file and the embedder (the spec's Live-verification constraint). The
script is authored and reviewable any time; execution is off-peak. It
drives every operation in the backend-parity list plus the HTTP-only
upload op through the SAME shared legs the CI parity test uses, and
prints ONE JSON LINE PER OPERATION on stdout:

    {"op": "...", "status": "...", "detail": "..."}

``success``: the envelope's status is a known-good outcome value.
``honest_refusal``: a typed envelope answering a documented non-good
outcome (unavailable, empty, partial, ...). ``fail``: an engine-shaped
exception or a non-envelope. Exit 0 only when nothing failed. Other
libraries also print to stdout: select records with grep '"op"'.
Usage: ``--list`` (offline op list) or ``--duck-file PATH`` (the run).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]

# The drift pin (tests/test_duck_live_matrix_script.py): this list must
# equal PARITY_OPS (tests/test_backend_operation_parity.py) plus the
# HTTP-only upload op. Keep the CI tuple authoritative; the pin test
# fails this script when they drift.
OPS: tuple[str, ...] = (
    "topics_list", "labeling_list", "labeling_lfs", "classification_list",
    "analysis_select_method", "query", "statements_list", "statements_timeline",
    "conflicts_list", "beliefs_current", "queue_enqueue", "queue_status",
    "ingest_status", "list_documents", "get_document_tags", "get_metadata",
    "add_entity", "add_relation", "search_graph", "get_entity_relations",
    "graph_neighbors", "graph_traverse", "release_assess", "notebook_evidence",
    "notebook_uncoded", "notebook_overlap", "delete_document",
    "project_provision", "project_list", "project_drop", "coding_status",
    "coding_export", "events_summary", "keyphrases_extract",
    "lexical_classify", "neighbors_classify", "centroids_classify",
    "clusters_inspect", "duplicates_screen", "outliers_rank",
    "sentiment_screen", "active_learn_next", "nli_classify",
    "contradictions_screen", "survey_openend_codes", "usability_severity",
    "emotion_tag", "labeling_build", "classification_build", "topics_build",
    "topics_trends", "analysis_backfill_dates", "analysis_embed_meta",
    "analyze_run", "search_context", "verify_answer", "notebook_ask",
    "conflicts_scan", "labeling_triage", "labeling_gold_check",
    "queue_cancel",
    "research_cycle", "research_demo", "demo_load", "extraction_tune",
    "ingest_url", "release_compare", "ingest_reingest", "ingest_directory", "ingest_upload",
)

GOOD_STATUSES = frozenset({"success", "completed", "compared", "skipped"})
# The three analysis-date legs run HOT (the dormant default would mask
# the date path behind a disabled envelope), exactly like the CI test.
HOT_OPS = frozenset({"topics_trends", "analysis_backfill_dates", "analyze_run"})
HOT_CONFIG: dict[str, object] = {"analysis": {"topics": {"over_time": {"enabled": True}}}}


def list_ops() -> list[str]:
    """The matrix op list (the offline drift pin and --list read this)."""
    return list(OPS)


def main(argv: list[str] | None = None) -> int:
    """The CLI entry: --list answers offline; --duck-file drives the run."""
    parser = argparse.ArgumentParser(description="The DuckDB live parity matrix.")
    parser.add_argument(
        "--list", action="store_true", help="print the op list as JSON (opens no store)"
    )
    parser.add_argument("--duck-file", help="the .duckdb file to migrate and drive")
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps({"mode": "list", "ops": list_ops()}))
        return 0
    if not args.duck_file:
        parser.error("--duck-file is required without --list")
    return asyncio.run(_run_matrix(Path(args.duck_file).resolve()))


async def _run_matrix(duck_file: Path) -> int:
    """Boot one migrated world, drive every op, print one record each."""
    for entry in (str(REPO_ROOT), str(REPO_ROOT / "tests")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    from test_surface_parity_deep import LEGS

    ctx, gateway = await _boot_world(duck_file)
    failures = 0
    try:
        for op in OPS:
            leg = LEGS.get(op)
            if leg is None:
                record = {"op": op, "status": "fail", "detail": "no shared leg"}
            else:
                leg_ctx = {**ctx, "config": HOT_CONFIG} if op in HOT_OPS else ctx
                record = await _drive(op, leg.shared, leg_ctx)
            print(json.dumps(record), flush=True)
            if record["status"] == "fail":
                failures += 1
    finally:
        await gateway.close()
        from corpus_kb.domain.application import reset_app

        reset_app()
    return 1 if failures else 0


async def _drive(
    op: str,
    shared: Callable[[dict[str, object]], Awaitable[object]],
    ctx: dict[str, object],
) -> dict[str, str]:
    """One shared leg -> one classified record (failures never stop the run)."""
    try:
        envelope = await shared(ctx)
    except Exception as exc:  # collected, never first-stop
        return {"op": op, "status": "fail", "detail": f"{type(exc).__name__}: {exc}"}
    if isinstance(envelope, dict) and "status" in envelope:
        status = str(envelope["status"])
        bucket = "success" if status in GOOD_STATUSES else "honest_refusal"
        return {"op": op, "status": bucket, "detail": status}
    shape = type(envelope).__name__
    return {"op": op, "status": "fail", "detail": f"envelope without a status key: {shape}"}


async def _boot_world(duck_file: Path) -> tuple[dict[str, object], object]:
    """The parity world: migrated file, duck boot, and the leg seeds.

    Mirrors tests/test_backend_operation_parity.py::parity_store plus the
    PR-8 seeds (drop dir, transcript dir, canned fetch, release world).
    """
    import yaml
    from test_surface_parity_deep import (
        _DIR_PATH,
        _QUEUE_ID,
        CV,
        DOC_ID,
        RELEASE_ID,
        TENANT,
        TEXT_BODY,
    )

    import corpus_kb.tools.ingest_url_tools as url_tools
    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.config import load_config
    from corpus_kb.domain.application import get_app_for_config
    from corpus_kb.domain.codebook import CodebookVersion
    from corpus_kb.handlers import release_handlers
    from corpus_kb.server_wiring_duckdb import startup_duckdb
    from corpus_kb.web.fetch import FetchedPage
    from live_embed import live_embed_dimensions, live_embed_model

    duck_file.parent.mkdir(parents=True, exist_ok=True)
    dsn = f"duckdb:///{duck_file.as_posix()}"
    run_duckdb_migrations(dsn)

    world = Path(tempfile.mkdtemp(prefix="duck-live-matrix-"))
    cfg_path = world / "config.yaml"
    cfg_path.write_text(
        "database:\n"
        "  backend: duckdb\n"
        f"  connection_string: {duck_file.as_posix()}\n"
        "graph:\n"
        "  backend: duckdb\n"
        "extraction:\n"
        "  backend: regex\n"
        # Name the embedder this run actually pulls; dimensions travel
        # with the model so the embedder's width validation passes.
        "embedding:\n"
        "  provider: ollama\n"
        f"  model: {live_embed_model()}\n"
        f"  dimensions: {live_embed_dimensions()}\n",
        encoding="utf-8",
    )
    os.environ["CORPUS_KB_CONFIG"] = str(cfg_path)
    os.environ["CORPUS_KB_DATABASE_URL"] = dsn

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    services = await startup_duckdb(cfg, dsn)
    gateway = services["gateway"]

    book = world / "queue-book.md"
    book.write_text(TEXT_BODY, encoding="utf-8")
    gold = world / "parity-gold.json"
    gold.write_text(json.dumps({"chunk-1": "reports"}), encoding="utf-8")
    drop_dir = world / "parity-drop"
    drop_dir.mkdir()
    (drop_dir / "parity-note.md").write_text("parity drop note\n", encoding="utf-8")
    transcript_dir = world / "parity-transcripts"
    transcript_dir.mkdir()
    (transcript_dir / "parity-interview.txt").write_text(
        "Alice: The dashboard reads clearly now.\nBob: Search feels faster this week.\n",
        encoding="utf-8",
    )

    await gateway.execute(
        "INSERT INTO documents (doc_id, tenant_id, source, source_type)"
        " VALUES ($1::uuid, $2, $3, $4)",
        DOC_ID, TENANT, "parity-seed", "text",
    )
    await gateway.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type)"
        " VALUES ($1::uuid, $2, $3::uuid, 0, $4, $5)",
        str(uuid4()), TENANT, DOC_ID, "parity seed text", "text",
    )
    await gateway.execute(
        "INSERT INTO ingest_queue (id, tenant_id, path, content_hash)"
        " VALUES ($1::uuid, $2, $3, $4)",
        _QUEUE_ID, TENANT, str(book) + "-cancel-seed", "parity-cancel-seed-content-hash",
    )

    page_html = "<html><body><p>parity page body</p></body></html>"

    async def canned_fetch(url: str, config: object, transport: object = None) -> FetchedPage:
        return FetchedPage(url, url, 200, page_html, "text/html", "2026-11-24T00:00:00+00:00")

    url_tools.fetch_page = canned_fetch

    app = get_app_for_config(load_config())
    aggregate = CodebookVersion._create(
        CodebookVersion.Created, id=UUID(CV), tenant_id=UUID(TENANT),
        label="parity", sha256="0" * 64, paradigm="deductive", notes="",
    )
    app.save(aggregate)
    await release_handlers.request_release(
        gateway, UUID(TENANT), UUID(CV), "parity", release_id=UUID(RELEASE_ID)
    )

    ctx: dict[str, object] = {
        "pool": gateway,
        "config": {},
        "file_path": str(book),
        "gold_path": str(gold),
        _DIR_PATH: str(drop_dir),
        "ingest_dir": str(transcript_dir),
    }
    return ctx, gateway


if __name__ == "__main__":
    sys.exit(main())
