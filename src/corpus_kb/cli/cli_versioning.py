"""Corpus stats CLI commands (Phase-2 Wave 3).

Two read commands over the shared tools, mirroring cli_statements: they
reuse the cli_ingest plumbing and import corpus_kb.cli nowhere, so the
import in cli.py stays acyclic.
"""

from __future__ import annotations

import json

import typer

from corpus_kb.cli._plumbing import _JSON_OPT, _TENANT_OPT, flow
from corpus_kb.cli.cli_ingest import _run_sync


def register(app: typer.Typer) -> None:
    """Attach the two stats commands to the main Typer app."""
    app.command("stats")(stats)
    app.command("document-stats")(document_stats)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    stats_map = result.get("stats", result.get("totals", {}))
    if isinstance(stats_map, dict):
        for name, count in stats_map.items():
            print(f"  {name}: {count}")
    rows = result.get("by_source_type", [])
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                print(f"  {row.get('source_type')}: {row.get('count')}")


def stats(
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Count the corpus rows, event store included."""
    kwargs = {"tenant_id": tenant_id}
    return _run_sync(lambda: flow("versioning_tools", "stats", kwargs, json_output, _emit))


def document_stats(
    tenant_id: str = _TENANT_OPT,
    json_output: bool = _JSON_OPT,
) -> int:
    """Aggregate the document statistics by source type."""
    kwargs = {"tenant_id": tenant_id}
    return _run_sync(lambda: flow("versioning_tools", "document_stats", kwargs, json_output, _emit))
