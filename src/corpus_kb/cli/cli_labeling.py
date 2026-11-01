"""The ``corpus-kb labeling`` front door (analysis program Wave 0).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb labeling-build`` — the weak-supervision run (the same
    handler the MCP + HTTP + socket surfaces call).
  * ``corpus-kb labeling-list`` — the built chunk labels.
  * ``corpus-kb labeling-lfs`` — the registered labeling functions.
  * ``corpus-kb labeling-triage`` — the weak-label review queue (U39).
  * ``corpus-kb labeling-gold-check`` — gold-set validation (F6).

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
from corpus_kb.cli._plumbing import EXIT_CONFIG, EXIT_FAILED, EXIT_OK
from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync


def register(app: typer.Typer) -> None:
    """Attach the five labeling commands to the main Typer app."""
    app.command("labeling-build")(labeling_build)
    app.command("labeling-list")(labeling_list)
    app.command("labeling-lfs")(labeling_lfs)
    app.command("labeling-triage")(labeling_triage)
    app.command("labeling-gold-check")(labeling_gold_check)


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


def _emit_quality(result: dict[str, object], name: str, json_output: bool) -> None:
    """Emit one label-quality envelope (triage or gold-check)."""
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status == "success" and "n_flagged" in result:
        queue = result.get("queue")
        shown = len(queue) if isinstance(queue, list) else 0
        print(f"{name}: {result.get('n_flagged', 0)} flagged; {shown} rows shown")
        return
    if status == "success":
        print(f"{name}: {result.get('class_count', 0)} classes validated")
        return
    print(f"{name}: {status}")
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


async def _call_triage(cv_folds: int, flag_threshold: float, limit: int, json_output: bool) -> int:
    """Run the triage op through the shared tool layer and emit it."""
    from corpus_kb.tools.labeling_triage_tools import labeling_triage

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await labeling_triage(
            pool,
            cfg,
            cv_folds=cv_folds,
            flag_threshold=flag_threshold,
            limit=limit,
        )
    finally:
        await pool.close()
    _emit_quality(result, "labeling-triage", json_output)
    return _exit_for(result)


async def _call_gold(gold_file: str, confidence_level: float, json_output: bool) -> int:
    """Run the gold-check op through the shared tool layer and emit it."""
    from corpus_kb.tools.labeling_gold_tools import labeling_gold_check

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await labeling_gold_check(
            pool,
            cfg,
            gold_file=gold_file,
            confidence_level=confidence_level,
        )
    finally:
        await pool.close()
    _emit_quality(result, "labeling-gold-check", json_output)
    return _exit_for(result)


def labeling_triage(
    cv_folds: int = typer.Option(5, "--cv-folds", min=2, help="Cross-validation folds"),
    flag_threshold: float = typer.Option(
        0.2, "--flag-threshold", min=0.001, max=1.0, help="Support level that flags a label"
    ),
    limit: int = typer.Option(50, "--limit", min=1, help="How many queue rows to show"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Rank weakly supported built labels into a human review queue.

    Advisory only; nothing is relabeled. Exit codes: 0 ok (including
    the honest no-op outcomes), 1 failed, 3 database down.
    """
    return _run_sync(lambda: _call_triage(cv_folds, flag_threshold, limit, json_output))


def labeling_gold_check(
    gold_file: str = typer.Option(
        ..., "--gold-file", help="Gold dev set: JSON object of chunk id to label name"
    ),
    confidence_level: float = typer.Option(
        0.95, "--confidence-level", min=0.001, max=0.999, help="Interval coverage"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Score built labels against a gold dev set, one class at a time.

    Exit codes: 0 ok (including the honest no-op outcomes), 1 failed,
    3 database down.
    """
    return _run_sync(lambda: _call_gold(gold_file, confidence_level, json_output))
