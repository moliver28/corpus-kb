"""Belief-op CLI commands (Living Corpus W4).

Four commands over the shared handlers, mirroring cli_conflicts: it
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
    """Attach the four belief commands to the main Typer app."""
    app.command("beliefs-current")(beliefs_current)
    app.command("beliefs-timeline")(beliefs_timeline)
    app.command("beliefs-predict")(beliefs_predict)
    app.command("beliefs-compare")(beliefs_compare)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"beliefs: {result.get('status', 'error')}")
    for key in ("count", "subject", "probability", "interval_low", "interval_high"):
        if key in result:
            print(f"  {key}: {result[key]}")


def beliefs_current(
    tenant_id: str = _TENANT_OPT,
    subject: str = typer.Option(None, "--subject", help="Filter by subject"),
    limit: int = typer.Option(50, "--limit", help="Max rows"),
    rebuild: bool = typer.Option(True, "--rebuild/--no-rebuild", help="Recompute first"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Current belief posteriors."""
    kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "subject": subject,
        "limit": limit,
        "rebuild": rebuild,
    }
    return _run_sync(lambda: flow("beliefs_tools", "beliefs_current", kwargs, json_output, _emit))


def beliefs_timeline(
    subject: str = typer.Option(..., "--subject", help="Belief subject"),
    relation: str = typer.Option(..., "--relation", help="Belief relation (template id)"),
    object: str = typer.Option(..., "--object", help="Belief object"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Era history for one belief."""
    kwargs = {
        "tenant_id": tenant_id,
        "subject": subject,
        "relation": relation,
        "object": object,
    }
    return _run_sync(lambda: flow("beliefs_tools", "beliefs_timeline", kwargs, json_output, _emit))


def beliefs_predict(
    subject: str = typer.Option(..., "--subject", help="Belief subject"),
    relation: str = typer.Option(..., "--relation", help="Belief relation (template id)"),
    object: str = typer.Option(..., "--object", help="Belief object"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Posterior predictive for one belief."""
    kwargs = {
        "tenant_id": tenant_id,
        "subject": subject,
        "relation": relation,
        "object": object,
    }
    return _run_sync(lambda: flow("beliefs_tools", "beliefs_predict", kwargs, json_output, _emit))


def beliefs_compare(
    subject: str = typer.Option(..., "--subject", help="Belief subject"),
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """One subject's beliefs across objects."""
    kwargs = {"tenant_id": tenant_id, "subject": subject}
    return _run_sync(lambda: flow("beliefs_tools", "beliefs_compare", kwargs, json_output, _emit))
