"""Statement-atom CLI commands (Living Corpus W2).

Two commands over the shared handlers, mirroring cli_analysis_methods:
it reuses the cli_ingest plumbing and imports corpus_kb.cli nowhere, so
the import in cli.py stays acyclic.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from corpus_kb.cli._plumbing import _JSON_OPT, _TENANT_OPT, flow
from corpus_kb.cli.cli_ingest import _run_sync


def register(app: typer.Typer) -> None:
    """Attach the two statement commands to the main Typer app."""
    app.command("statements-list")(statements_list)
    app.command("statements-timeline")(statements_timeline)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"statements: {result.get('status', 'error')}")
    rows = result.get("statements", [])
    if isinstance(rows, list):
        for row in rows[:10]:
            if isinstance(row, dict):
                print(f"  [{row.get('template_id')}] {row.get('text')}")


def statements_list(
    tenant_id: str = _TENANT_OPT,
    template_id: str = typer.Option(None, "--template", help="Filter by claim template id"),
    document_id: str = typer.Option(None, "--document-id", help="Filter by document"),
    limit: int = typer.Option(50, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """List statement atoms newest-first."""
    kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "template_id": template_id,
        "document_id": document_id,
        "limit": limit,
    }
    return _run_sync(
        lambda: flow("statements_tools", "statements_list", kwargs, json_output, _emit)
    )


def statements_timeline(
    tenant_id: str = _TENANT_OPT,
    text_query: str = typer.Option(None, "--text", help="Substring to match"),
    limit: int = typer.Option(200, "--limit", help="Max rows to return"),
    json_output: bool = _JSON_OPT,
) -> int:
    """Chronological statement history — the belief-history view."""
    kwargs = {
        "tenant_id": tenant_id,
        "text_query": text_query,
        "limit": limit,
    }
    return _run_sync(
        lambda: flow("statements_tools", "statements_timeline", kwargs, json_output, _emit)
    )
