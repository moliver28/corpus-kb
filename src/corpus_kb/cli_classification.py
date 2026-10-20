"""The ``corpus-kb classification`` front door (analysis program Wave 0).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb classification-build`` — the few-shot run (the same
    handler the MCP + HTTP + socket surfaces call).
  * ``corpus-kb classification-list`` — the built chunk predictions.

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
    """Attach the two classification commands to the main Typer app."""
    app.command("classification-build")(classification_build)
    app.command("classification-list")(classification_list)


def _exit_for(result: dict[str, object]) -> int:
    """Map a classification envelope to the honest exit code."""
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
        print(f"classification: {result.get('prediction_count', 0)} predictions; status {status}")
        return
    print(f"classification: {status}")
    reason = result.get("reason")
    if reason:
        print(f"  {reason}")
    remedy = result.get("remedy")
    if remedy:
        print(f"  remedy: {remedy}")


async def _build_flow(seed_file: str | None, json_output: bool) -> int:
    from corpus_kb.tools.classification_tools import classification_build

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await classification_build(pool, cfg, seed_file=seed_file)
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


async def _list_flow(limit: int, json_output: bool) -> int:
    from corpus_kb.tools.classification_tools import classification_list

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await classification_list(pool, cfg, limit=limit)
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


def classification_build(
    seed_file: str | None = typer.Option(
        None, "--seed-file", help="Path to the seed label set (JSON)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Fit the few-shot classifier; write the calibrated predictions.

    Requires analysis.classification.enabled: true. Exit codes: 0 built
    (or an honest no-op: disabled, engine not built), 1 the run failed,
    3 database down.
    """
    return _run_sync(lambda: _build_flow(seed_file, json_output))


def classification_list(
    limit: int = typer.Option(50, "--limit", min=1, help="How many predictions to show"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List the built chunk predictions, bounded by limit.

    Exit codes: 0 ok (including the disabled no-op), 1 failed, 3
    database down.
    """
    return _run_sync(lambda: _list_flow(limit, json_output))
