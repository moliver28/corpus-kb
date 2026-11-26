"""Op-by-op DuckDB execution matrix — the parity PR-8 live driver (U1/U2).

COMMITTED, RUN OFF-PEAK ONLY: the live run needs a real migrated DuckDB
file and the embedder; authoring and review are peak-safe. It drives
every op in the backend-parity list plus the HTTP-only upload op via
the SAME shared legs the CI parity test uses, printing one JSON line
per op: {"op", "status", "detail"}. ``success``: a known-good outcome.
``honest_refusal``: a typed refusal envelope. ``fail``: an exception,
a non-envelope, or a failed or errored status. Exit 0 only when
nothing failed. Other libraries print to stdout too; grep '"op"'.
Usage: ``--list`` (offline), or ``--duck-file PATH`` (the run). The run
writes parity seeds; it refuses a used file without ``--allow-nonempty``.
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

from corpus_kb.errors import ConflictError
from corpus_kb.ops_backend_matrix import declared_ops

REPO_ROOT = Path(__file__).resolve().parents[1]

# Drift pin: OPS must equal PARITY_OPS + ingest_upload; the pin test fails
# drift. Derived from the declared op matrix, so one universe serves both:
# the declared order is stable, and only set equality is pinned.
OPS: tuple[str, ...] = declared_ops()

GOOD_STATUSES = frozenset({"success", "completed", "compared", "skipped"})
FAIL_STATUSES = frozenset({"failed", "error"})
# The analysis-date legs run HOT; the dormant default would mask the date path.
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
    parser.add_argument("--duck-file", help="file to drive (writes seeds; refuses a used file)")
    parser.add_argument("--allow-nonempty", action="store_true", help="drive a used file")
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps({"mode": "list", "ops": list_ops()}))
        return 0
    if not args.duck_file:
        parser.error("--duck-file is required without --list")
    try:
        return asyncio.run(_run_matrix(Path(args.duck_file).resolve(), args.allow_nonempty))
    except ConflictError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2


async def _run_matrix(duck_file: Path, allow_nonempty: bool) -> int:
    """Boot one migrated world, drive every op, print one record each."""
    for entry in (str(REPO_ROOT), str(REPO_ROOT / "tests")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    from test_surface_parity_deep import LEGS

    ctx, gateway = await _boot_world(duck_file, allow_nonempty)
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
        if status in FAIL_STATUSES:
            return {"op": op, "status": "fail", "detail": status}
        bucket = "success" if status in GOOD_STATUSES else "honest_refusal"
        return {"op": op, "status": bucket, "detail": status}
    shape = type(envelope).__name__
    return {"op": op, "status": "fail", "detail": f"envelope without a status key: {shape}"}


async def _boot_world(duck_file: Path, allow_nonempty: bool) -> tuple[dict[str, object], object]:
    """Boot the parity world (mirrors parity_store); guard, then seed."""
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
        # Name the embedder this run pulls; dimensions travel with the model.
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
    # Momus fix 1: guard before every write; --allow-nonempty is the opt-in.
    stored = await gateway.fetchval("SELECT count(*) FROM documents")
    if stored and not allow_nonempty:
        raise ConflictError("The file already holds documents. Pass --allow-nonempty to proceed.")

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
        DOC_ID,
        TENANT,
        "parity-seed",
        "text",
    )
    await gateway.execute(
        "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type)"
        " VALUES ($1::uuid, $2, $3::uuid, 0, $4, $5)",
        str(uuid4()),
        TENANT,
        DOC_ID,
        "parity seed text",
        "text",
    )
    await gateway.execute(
        "INSERT INTO ingest_queue (id, tenant_id, path, content_hash)"
        " VALUES ($1::uuid, $2, $3, $4)",
        _QUEUE_ID,
        TENANT,
        str(book) + "-cancel-seed",
        "parity-cancel-seed-content-hash",
    )

    page_html = "<html><body><p>parity page body</p></body></html>"

    async def canned_fetch(url: str, config: object, transport: object = None) -> FetchedPage:
        return FetchedPage(url, url, 200, page_html, "text/html", "2026-11-24T00:00:00+00:00")

    url_tools.fetch_page = canned_fetch

    app = get_app_for_config(load_config())
    aggregate = CodebookVersion._create(
        CodebookVersion.Created,
        id=UUID(CV),
        tenant_id=UUID(TENANT),
        label="parity",
        sha256="0" * 64,
        paradigm="deductive",
        notes="",
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
