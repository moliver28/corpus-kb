"""The ``corpus-kb analysis`` front door (analysis program Wave 0).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb analysis-backfill-dates`` — the one-shot idempotent
    ``documents.document_date`` backfill.
  * ``corpus-kb analyze-run`` — the composed analysis run (the same
    handler the MCP + HTTP + socket surfaces call).

HONEST EXIT CODES (stable for scripting): ``0`` the command answered
(including the honest no-op outcomes: disabled feature, unavailable
engine), ``1`` the command failed, ``3`` configuration state (migration
missing, unreachable database). A DISABLED feature exits 0: the
documented default state is "dormant", not broken — the printed envelope
names the flag and the remedy. Mirrors ``cli_topics.py``.
"""

from __future__ import annotations

import json

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 3


def register(app: typer.Typer) -> None:
    """Attach the analysis commands to the main Typer app."""
    app.command("analysis-backfill-dates")(analysis_backfill_dates)
    app.command("analyze-run")(analyze_run)
    app.command("analysis-embed-meta")(analysis_embed_meta)


def _exit_for(result: dict[str, object]) -> int:
    """Map an analysis envelope to the honest exit code."""
    status = str(result.get("status", "error"))
    if status in ("disabled", "success"):
        return EXIT_OK
    if status == "unavailable":
        return EXIT_CONFIG
    return EXIT_FAILED


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status == "success":
        print(f"analysis: status {status}")
        return
    print(f"analysis: {status}")
    reason = result.get("reason")
    if reason:
        print(f"  {reason}")
    remedy = result.get("remedy")
    if remedy:
        print(f"  remedy: {remedy}")


async def _backfill_flow(json_output: bool) -> int:
    from corpus_kb.tools.analysis_tools import analysis_backfill_dates

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await analysis_backfill_dates(pool, cfg)
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


async def _run_flow(output: str | None, json_output: bool) -> int:
    from corpus_kb.tools.analysis_tools import analyze_run

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await analyze_run(pool, cfg, output=output)
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


def analysis_backfill_dates(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Backfill documents.document_date from chunk metadata (idempotent).

    Gated behind analysis.topics.over_time.enabled. Exit codes: 0 done
    (or an honest no-op: disabled, engine not built), 1 the run failed,
    3 database down.
    """
    return _run_sync(lambda: _backfill_flow(json_output))


def analyze_run(
    output: str | None = typer.Option(
        None, "--output", help="Path for the written analysis report"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Run the composed corpus analysis; write the report artifact.

    Exit codes: 0 done (or an honest no-op: disabled, engines not
    built), 1 the run failed, 3 database down.
    """
    return _run_sync(lambda: _run_flow(output, json_output))


async def _meta_embed_flow(kinds: str | None, json_output: bool) -> int:
    from corpus_kb.tools.meta_embed_tools import analysis_embed_meta

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await analysis_embed_meta(pool, cfg, kinds=kinds)
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


def analysis_embed_meta(
    kinds: str | None = typer.Option(
        None,
        "--kinds",
        help="Comma list: document,topic,belief_summary,profile (default all)",
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Build the coarse corpus meta embeddings (the search meta arm).

    Exit codes: 0 done, 1 the build failed, 3 database down.
    """
    return _run_sync(lambda: _meta_embed_flow(kinds, json_output))
