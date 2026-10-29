"""Queue-op CLI commands (textbook-ingestion program, Wave 1).

Three commands over the shared handlers, mirroring cli_conflicts: it
reuses the cli_ingest plumbing and imports corpus_kb.cli nowhere, so
the import in cli/__init__ stays acyclic.
"""

from __future__ import annotations

import json

import typer

from corpus_kb.cli._plumbing import _JSON_OPT, _TENANT_OPT, EXIT_FAILED, flow
from corpus_kb.cli.cli_ingest import _run_sync


def register(app: typer.Typer) -> None:
    """Attach the three queue commands to the main Typer app."""
    app.command("queue-enqueue")(queue_enqueue)
    app.command("queue-status")(queue_status)
    app.command("queue-cancel")(queue_cancel)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"queue: {result.get('status', 'error')}")
    for key in ("queue_id", "content_hash", "deduplicated", "state", "count"):
        if key in result:
            print(f"  {key}: {result[key]}")
    entries = result.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                print(f"  - {entry.get('id')} {entry.get('status')} {entry.get('path')}")


def _parse_strategy_config(raw: str | None) -> dict[str, object] | None:
    """Parse the --strategy-config JSON text; None passes through."""
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"strategy-config must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("strategy-config must be a JSON object.")
    return parsed


def queue_enqueue(
    path: str = typer.Argument(..., help="The file path to queue"),
    title: str = typer.Option(None, "--title", help="Display title"),
    profile: str = typer.Option(None, "--profile", help="Corpus profile name"),
    strategy_config_json: str = typer.Option(
        None, "--strategy-config", help="Strategy config as a JSON object"
    ),
    project_id: str = typer.Option(None, "--project-id", help="Research project id"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Queue one file for ingestion (idempotent per content hash)."""
    try:
        strategy_config = _parse_strategy_config(strategy_config_json)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        print('  Pass a JSON object, for example: --strategy-config "{\\"k\\": 1}"')
        return EXIT_FAILED
    kwargs: dict[str, object] = {
        "path": path,
        "title": title,
        "profile": profile,
        "strategy_config": strategy_config,
        "project_id": project_id,
    }
    return _run("queue_enqueue", kwargs, json_output)


def queue_status(
    queue_id: str = typer.Argument(None, help="One queue entry id (omit to list)"),
    limit: int = typer.Option(50, "--limit", help="Max rows to return"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Read one queue row, or the pending and running rows."""
    kwargs: dict[str, object] = {"queue_id": queue_id, "limit": limit}
    return _run("queue_status", kwargs, json_output)


def queue_cancel(
    queue_id: str = typer.Argument(..., help="The queue entry id"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Cancel one pending or running queue row."""
    kwargs: dict[str, object] = {"queue_id": queue_id}
    return _run("queue_cancel", kwargs, json_output)


def _run(op_name: str, kwargs: dict[str, object], json_output: bool) -> int:
    """One command flow with the module's emit (the plumbing seam)."""
    return _run_sync(lambda: flow("queue_tools", op_name, dict(kwargs), json_output, _emit))
