"""The ``corpus-kb demo-kb`` front door (UX program Wave 6).

One command loads the bundled general-RAG sample corpus
(``docs/demo-kb``) through the standard ingest tool layer, so a fresh
database has something to search before any real files arrive.

A lifecycle command in the quickstart/eval family — NOT a catalogue
operation (the ``cli_quickstart`` precedent). Registry rows wait for
Task 5b, which lands the MCP legs and the rows in one change.

HONEST EXIT CODES: ``0`` every sample file ingested or skipped unchanged,
``1`` the corpus is missing or some file failed (the receipt says which).
"""

from __future__ import annotations

import asyncio
import json

import typer

from corpus_kb.cli._plumbing import EXIT_FAILED, EXIT_OK


def register(app: typer.Typer) -> None:
    """Attach the demo-kb command to the main Typer app."""
    app.command("demo-kb")(demo_kb)


def _print_receipt(envelope: dict[str, object]) -> None:
    """The human receipt: one line per file, then the totals."""
    result = envelope.get("result")
    result_dict = result if isinstance(result, dict) else {}
    ingested = result_dict.get("ingested")
    skipped = result_dict.get("skipped")
    chunk_counts = result_dict.get("chunk_counts")
    ingested_list = [str(name) for name in ingested] if isinstance(ingested, list) else []
    skipped_list = [str(name) for name in skipped] if isinstance(skipped, list) else []
    counts = (
        {str(k): int(v) for k, v in chunk_counts.items()} if isinstance(chunk_counts, dict) else {}
    )
    for name in ingested_list:
        print(f"[OK] {name}: {counts.get(name, 0)} chunk(s)")
    for name in skipped_list:
        print(f"[SKIP] {name}: unchanged")
    print(f"ingested {len(ingested_list)}, skipped {len(skipped_list)}")


def _failures_line(envelope: dict[str, object]) -> str:
    """One 'name: message' entry per failed file, from result.failures."""
    result = envelope.get("result")
    result_dict = result if isinstance(result, dict) else {}
    failures = result_dict.get("failures")
    if not isinstance(failures, dict):
        return ""
    return "; ".join(f"{name}: {failures[name]}" for name in sorted(failures))


def demo_kb(
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Load the sample knowledge corpus into the database.

    Ingests every markdown file under docs/demo-kb through the standard
    pipeline. Exit codes: 0 loaded, 1 missing corpus or a failed file.
    """
    from corpus_kb.config import load_config
    from corpus_kb.demo_kb import DemoCorpusMissingError, load_demo_kb

    try:
        envelope = asyncio.run(load_demo_kb(load_config()))
    except DemoCorpusMissingError as exc:
        print(f"ERROR: {exc}")
        print(f"Fix: {exc.remediation}")
        raise typer.Exit(code=EXIT_FAILED) from None
    if json_output:
        print(json.dumps(envelope, indent=2))
    else:
        _print_receipt(envelope)
        failures = _failures_line(envelope)
        if failures:
            print(f"PARTIAL: {failures}")
    status = str(envelope.get("status", "error"))
    if status != "success":
        raise typer.Exit(code=EXIT_FAILED)
    return EXIT_OK


__all__ = ["demo_kb", "register"]
