"""Tune-op CLI command (Living Corpus W5).

One command over the shared handler, mirroring cli_conflicts: it
reuses the cli_ingest plumbing and imports corpus_kb.cli nowhere, so
the import in cli.py stays acyclic.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from corpus_kb.cli._plumbing import _JSON_OPT, _TENANT_OPT, flow
from corpus_kb.cli.cli_ingest import _run_sync

# The tune op has no honest no-op statuses: anything but "success" is a
# failure worth an exit 1 (the shared flow's default set is wider).
_TUNE_OK = ("success",)


def register(app: typer.Typer) -> None:
    """Attach the tune command to the main Typer app."""
    app.command("extraction-tune")(extraction_tune)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"extraction-tune: {result.get('status', 'error')}")
    for key in (
        "method",
        "profile",
        "examples_used",
        "exemplars_written",
        "seeds_path",
        "adapter_path",
    ):
        if result.get(key) is not None:
            print(f"  {key}: {result[key]}")
    notes = result.get("notes")
    if isinstance(notes, list):
        for note in notes:
            print(f"  note: {note}")


def extraction_tune(
    method: str = typer.Option(
        "exemplar", help="exemplar (profile seeds) or lora (adapter training)"
    ),
    profile: str = typer.Option(None, help="Target profile name (creates or updates its YAML)"),
    min_confidence: float = typer.Option(0.8, help="Minimum relation confidence to mine"),
    limit: int = typer.Option(8, help="Maximum examples to mine"),
    dry_run: bool = typer.Option(False, help="Preview counts and paths; write nothing"),
    json_output: bool = _JSON_OPT,
    tenant_id: str = _TENANT_OPT,
) -> int:
    """Mine confirmed corpus decisions into tuning artifacts."""
    kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "method": method,
        "profile": profile,
        "min_confidence": min_confidence,
        "limit": limit,
        "dry_run": dry_run,
    }
    return _run_sync(
        lambda: flow(
            "extraction_tune_tools",
            "extraction_tune",
            kwargs,
            json_output,
            _emit,
            ok_statuses=_TUNE_OK,
        )
    )
