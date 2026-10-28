"""Conflict-op CLI commands (Living Corpus W3).

Three commands over the shared handlers, mirroring cli_statements: it
reuses the cli_ingest plumbing and imports corpus_kb.cli nowhere, so
the import in cli.py stays acyclic.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from corpus_kb.cli._plumbing import _JSON_OPT, _TENANT_OPT, flow
from corpus_kb.cli.cli_ingest import _run_sync


def register(app: typer.Typer) -> None:
    """Attach the three conflict commands to the main Typer app."""
    app.command("conflicts-scan")(conflicts_scan)
    app.command("conflicts-list")(conflicts_list)
    app.command("conflicts-resolve")(conflicts_resolve)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"conflicts: {result.get('status', 'error')}")
    for key in ("scanned", "flagged", "count", "resolved_verdict"):
        if key in result:
            print(f"  {key}: {result[key]}")


def conflicts_scan(
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Run the contradiction cascade over live statements."""
    kwargs: dict[str, Any] = {"tenant_id": tenant_id}
    return _run_sync(lambda: flow("conflicts_tools", "conflicts_scan", kwargs, json_output, _emit))


def conflicts_list(
    tenant_id: str = _TENANT_OPT,
    verdict: str = typer.Option(None, "--verdict", help="conflict|revision|uncertain"),
    limit: int = typer.Option(50, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """List recorded conflict flags newest-first."""
    kwargs = {"tenant_id": tenant_id, "verdict": verdict, "limit": limit}
    return _run_sync(lambda: flow("conflicts_tools", "conflicts_list", kwargs, json_output, _emit))


def conflicts_resolve(
    conflict_id: str = typer.Argument(..., help="The conflict flag id"),
    resolved_verdict: str = typer.Option(..., "--verdict", help="conflict|revision|dismissed"),
    resolved_by: str = typer.Option(..., "--reviewer", help="Who decided"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Finalize one conflict flag."""
    kwargs = {
        "tenant_id": tenant_id,
        "conflict_id": conflict_id,
        "resolved_verdict": resolved_verdict,
        "resolved_by": resolved_by,
    }
    return _run_sync(
        lambda: flow("conflicts_tools", "conflicts_resolve", kwargs, json_output, _emit)
    )
