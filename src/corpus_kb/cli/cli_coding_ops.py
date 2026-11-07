"""The chunk-coding CLI commands (issue #350).

Eight subcommands on the ``coding`` group, each a thin adapter over the
ONE shared tool function (``tools.coding_tools``) — the same function
the MCP tool, the socket arm, and the HTTP route serve:

  * ``corpus-kb coding embed-codebook FILE``
  * ``corpus-kb coding pool --codebook-version-id <uuid>``
  * ``corpus-kb coding code-batch --cells FILE``
  * ``corpus-kb coding reliability``
  * ``corpus-kb coding calibrate-floors LABELS``
  * ``corpus-kb coding saturation``
  * ``corpus-kb coding status``
  * ``corpus-kb coding export [--format csv]``

HONEST EXIT CODES: ``0`` the command answered, ``2`` the tables are
missing (unavailable), ``1`` any other failure. Every outcome is printed
as JSON, or CSV for ``export --format csv``.

This module attaches itself through :func:`register_ops` (called from
``cli_coding.py``), so the coding group's registration order stays in
one place.
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import typer

from corpus_kb.cli.cli_store import wired_store
from corpus_kb.cli.cli_support import DEFAULT_TENANT, _run_async, _tenant

__all__ = ["register_ops"]

_EXIT_ERROR = 1
_EXIT_UNAVAILABLE = 2


def _print_envelope(envelope: dict[str, Any]) -> int:
    """Print one tool envelope as JSON; map its status to the exit code."""
    print(json.dumps(envelope, indent=2, default=str))
    if envelope.get("status") == "unavailable":
        return _EXIT_UNAVAILABLE
    return 0 if envelope.get("status") == "success" else _EXIT_ERROR


def _print_csv(envelope: dict[str, Any]) -> int:
    """Print the export rows as CSV (the columns row is the header)."""
    writer = csv.writer(sys.stdout)
    columns = [str(c) for c in envelope.get("columns", [])]
    writer.writerow(columns)
    for row in envelope.get("rows", []):
        writer.writerow([row.get(column) for column in columns])
    if envelope.get("status") == "unavailable":
        return _EXIT_UNAVAILABLE
    return 0 if envelope.get("status") == "success" else _EXIT_ERROR


def _run_tool(fn: Any, kwargs: dict[str, Any], csv_out: bool = False) -> Coroutine[Any, Any, int]:
    """One shared runner: wired pool, default tenant, printed envelope."""

    async def _run() -> int:
        kwargs.setdefault("tenant_id", str(_tenant() or DEFAULT_TENANT))
        async with wired_store() as wired:
            envelope = await fn(wired.store, **kwargs)
        return _print_csv(envelope) if csv_out else _print_envelope(envelope)

    return _run()


def _read_json(path: str, what: str) -> Any:
    """Load one JSON document from disk; a readable error, never a traceback."""
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot read {what} file {path}: {exc}")
        raise typer.Exit(_EXIT_ERROR) from exc


def register_ops(coding_app: typer.Typer) -> None:
    """Attach the eight coding op commands to the coding group."""

    @coding_app.command("embed-codebook")
    def embed_codebook(
        file: str = typer.Argument(..., help="Codebook JSON file"),
    ) -> int:
        """Validate, embed, and load a codebook; prints its version id."""
        from corpus_kb.tools.coding_tools import coding_embed_codebook

        codebook = _read_json(file, "codebook")
        return _run_async(_run_tool(coding_embed_codebook, {"codebook": codebook}))

    @coding_app.command("pool")
    def pool(
        codebook_version_id: str = typer.Option(
            ..., "--codebook-version-id", help="Codebook version UUID to pool for"
        ),
    ) -> int:
        """Run pooling (keyword hits, similarity) and coverage reconciliation."""
        from corpus_kb.tools.coding_tools import coding_pool

        return _run_async(_run_tool(coding_pool, {"codebook_version_id": codebook_version_id}))

    @coding_app.command("code-batch")
    def code_batch(
        cells: str = typer.Option(..., "--cells", help="Cells JSON file (list of cells)"),
        coder: str = typer.Option(None, "--coder", help="Coder model tag"),
        model: str = typer.Option(None, "--model", help="Embedding model tag"),
        batch_id: str = typer.Option(None, "--batch-id", help="Batch label for the ledger"),
        coding_pass: int = typer.Option(1, "--coding-pass", help="Coding pass number"),
    ) -> int:
        """Code a batch of (chunk, code) cells with confidence routing."""
        from corpus_kb.tools.coding_tools import coding_code_batch

        parsed = _read_json(cells, "cells")
        return _run_async(
            _run_tool(
                coding_code_batch,
                {
                    "cells": parsed,
                    "coder": coder,
                    "model": model,
                    "batch_id": batch_id,
                    "coding_pass": coding_pass,
                },
            )
        )

    @coding_app.command("reliability")
    def reliability(
        per_code_min: int = typer.Option(5, "--per-code-min", help="Sampled rows per code"),
        seed: int = typer.Option(17, "--seed", help="Sampling seed"),
    ) -> int:
        """Sample the coded corpus and compute alpha/kappa reliability."""
        from corpus_kb.tools.coding_tools import coding_reliability

        return _run_async(
            _run_tool(
                coding_reliability,
                {"per_code_min": per_code_min, "seed": seed},
            )
        )

    @coding_app.command("calibrate-floors")
    def calibrate_floors(
        labels: str = typer.Argument(..., help="Labels JSON file (code_id + chunk ids)"),
        min_pos: int = typer.Option(10, "--min-pos", help="Positive examples wanted"),
        global_fallback: float = typer.Option(
            None, "--global-fallback", help="Floor used when positives are short"
        ),
    ) -> int:
        """Calibrate one code's pool/residual floors from labeled chunks."""
        from corpus_kb.tools.coding_tools import coding_calibrate_floors

        parsed = _read_json(labels, "labels")
        if not isinstance(parsed, dict):
            # Honest exit: typer ignores plain int returns, so non-zero raises.
            print("ERROR: labels file must hold a JSON object")
            raise typer.Exit(_EXIT_ERROR)
        return _run_async(_run_tool(coding_calibrate_floors, {**parsed, "min_pos": min_pos}))

    @coding_app.command("saturation")
    def saturation(
        batch_id: str = typer.Option(None, "--batch-id", help="Batch for the stop rule"),
        threshold: float = typer.Option(0.05, "--threshold", help="Stop-rule threshold"),
        min_samples: int = typer.Option(50, "--min-samples", help="Stop-rule sample floor"),
    ) -> int:
        """Report the ISR saturation signal and the batch stop rule."""
        from corpus_kb.tools.coding_tools import coding_saturation

        return _run_async(
            _run_tool(
                coding_saturation,
                {
                    "batch_id": batch_id,
                    "threshold": threshold,
                    "min_samples": min_samples,
                },
            )
        )

    @coding_app.command("status")
    def status() -> int:
        """Counts, saturation ratio, coverage by disposition, routes."""
        from corpus_kb.tools.coding_tools import coding_status

        return _run_async(_run_tool(coding_status, {}))

    @coding_app.command("export")
    def export(
        limit: int = typer.Option(1000, "--limit", help="Row cap (1-10000)"),
        offset: int = typer.Option(0, "--offset", help="Rows to skip"),
        fmt: str = typer.Option("json", "--format", help="Output format: json|csv"),
    ) -> int:
        """Accepted codes joined with chunk text and document source."""
        from corpus_kb.tools.coding_tools import coding_export

        csv_out = fmt == "csv"
        if not csv_out and fmt != "json":
            # Honest exit: typer ignores plain int returns, so non-zero raises.
            print(f"ERROR: unknown format {fmt!r}. Use json or csv.")
            raise typer.Exit(_EXIT_ERROR)
        return _run_async(
            _run_tool(coding_export, {"limit": limit, "offset": offset}, csv_out=csv_out)
        )
