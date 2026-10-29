"""The textbook scan front door (textbook-ingestion program, Wave 2C).

``corpus-kb textbook scan <path>`` walks recursive *.pdf and prints the
candidacy table (score desc, path asc). A lifecycle command family, not
a catalogue op: the scan core lives in ``ingestion.textbook_scan``, this
module owns output and the pypdf log filter only. It imports
corpus_kb.cli nowhere, so the cli/__init__ registration stays acyclic.
"""

from __future__ import annotations

import json
import logging

import typer

from corpus_kb.cli._plumbing import EXIT_OK
from corpus_kb.cli.cli_ingest import _finish
from corpus_kb.ingestion.textbook_scan import ScanResult, scan_path

textbook_app = typer.Typer(help="Textbook scans over local PDF files")


class _FontChatterFilter(logging.Filter):
    """Drop the fontTools warning flood pypdf emits on some PDFs."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "fonttools" not in record.getMessage().lower()


def _quiet_pdf_chatter() -> None:
    """Attach the font-chatter filter to the pypdf loggers (CLI layer)."""
    for name in ("pypdf", "pypdf._cmap"):
        logging.getLogger(name).addFilter(_FontChatterFilter())


def register(app: typer.Typer) -> None:
    """Attach the textbook command family to the main Typer app."""
    app.add_typer(textbook_app, name="textbook")


@textbook_app.command("scan")
def scan(
    path: str = typer.Argument(..., help="A PDF file, or a directory to walk for *.pdf"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Score PDFs for textbook signals; list candidates by score."""
    from corpus_kb.errors import CorpusError

    _quiet_pdf_chatter()
    try:
        results = scan_path(path)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        print(f"  {exc.remediation}")
        return _finish(exc.cli_exit)
    if json_output:
        print(json.dumps(_as_dict(results), indent=2))
        return EXIT_OK
    candidates = [result for result in results if result.candidate]
    print(f"textbook scan: {len(results)} pdf files, {len(candidates)} candidates")
    for result in results:
        band = "" if result.candidate else " (not a candidate)"
        note = f" {result.detail}" if result.detail else ""
        print(f"  {result.score:.2f}  {result.pages:>5} pages  {result.path}{band}{note}")
    return EXIT_OK


def _as_dict(results: list[ScanResult]) -> dict[str, object]:
    """The machine shape: one row per PDF, scan order preserved."""
    rows: list[dict[str, object]] = [
        {
            "path": result.path,
            "pages": result.pages,
            "score": result.score,
            "candidate": result.candidate,
            "signals": dict(result.signals),
            "detail": result.detail,
        }
        for result in results
    ]
    return {"status": "success", "count": len(rows), "results": rows}
