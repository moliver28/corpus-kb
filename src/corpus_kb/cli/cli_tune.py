"""Tune-op CLI command (Living Corpus W5).

One command over the shared handler, mirroring cli_conflicts: it
reuses the cli_ingest plumbing and imports corpus_kb.cli nowhere, so
the import in cli.py stays acyclic.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import typer

from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1

_OK_STATUSES = ("success",)

_JSON_OPT = typer.Option(False, "--json", help="Machine-readable JSON output")
_TENANT_OPT = typer.Option("00000000-0000-0000-0000-000000000001", "--tenant-id", help="Tenant id")


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


async def _flow(module_name: str, op_name: str, kwargs: dict[str, Any], json_output: bool) -> int:
    """One command flow: load config, open the pool, call, emit, map."""
    from corpus_kb.errors import CorpusError

    module = __import__(f"corpus_kb.tools.{module_name}", fromlist=[op_name])
    handler: Callable[..., Awaitable[dict[str, object]]] = getattr(module, op_name)
    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await handler(pool, cfg, **kwargs)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        remediation = getattr(exc, "remediation", None)
        if remediation:
            print(f"  {remediation}")
        return EXIT_FAILED
    _emit(result, json_output)
    return EXIT_OK if result.get("status") in _OK_STATUSES else EXIT_FAILED


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
    return _run_sync(
        lambda: _flow(
            "extraction_tune_tools",
            "extraction_tune",
            {
                "tenant_id": tenant_id,
                "method": method,
                "profile": profile,
                "min_confidence": min_confidence,
                "limit": limit,
                "dry_run": dry_run,
            },
            json_output,
        )
    )
