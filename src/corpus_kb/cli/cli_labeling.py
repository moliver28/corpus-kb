"""The ``corpus-kb labeling`` front door (analysis program Wave 0).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb labeling-build`` — the weak-supervision run (the same
    handler the MCP + HTTP + socket surfaces call).
  * ``corpus-kb labeling-list`` — the built chunk labels.
  * ``corpus-kb labeling-lfs`` — the registered labeling functions.

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
from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 3


def register(app: typer.Typer) -> None:
    """Attach the three labeling commands to the main Typer app."""
    app.command("labeling-build")(labeling_build)
    app.command("labeling-list")(labeling_list)
    app.command("labeling-lfs")(labeling_lfs)


def _exit_for(result: dict[str, object]) -> int:
    """Map a labeling envelope to the honest exit code."""
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
        print(f"labeling: {result.get('label_count', 0)} labels; status {status}")
        return
    print(f"labeling: {status}")
    reason = result.get("reason")
    if reason:
        print(f"  {reason}")
    remedy = result.get("remedy")
    if remedy:
        print(f"  remedy: {remedy}")


async def _call(tool_name: str, limit: int, json_output: bool) -> int:
    """Run one labeling op through the shared tool layer and emit it."""
    from corpus_kb.tools import labeling_tools

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        tool = getattr(labeling_tools, tool_name)
        if tool_name == "labeling_build":
            result = await tool(pool, cfg)
        else:
            result = await tool(pool, cfg, limit=limit)
    finally:
        await pool.close()
    _emit(result, json_output)
    return _exit_for(result)


def labeling_build(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Score the labeling functions and write the aggregated chunk labels.

    Requires analysis.labeling.enabled: true. Exit codes: 0 built (or an
    honest no-op: disabled, engine not built), 1 the run failed, 3
    database down.
    """
    return _run_sync(lambda: _call("labeling_build", 50, json_output))


def labeling_list(
    limit: int = typer.Option(50, "--limit", min=1, help="How many labels to show"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List the built chunk labels with probabilities.

    Exit codes: 0 ok (including the disabled no-op), 1 failed, 3
    database down.
    """
    return _run_sync(lambda: _call("labeling_list", limit, json_output))


def labeling_lfs(
    limit: int = typer.Option(50, "--limit", min=1, help="How many functions to show"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List the registered labeling functions with kinds and coverage.

    Exit codes: 0 ok (including the disabled no-op), 1 failed, 3
    database down.
    """
    return _run_sync(lambda: _call("labeling_lfs", limit, json_output))
