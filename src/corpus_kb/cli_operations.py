"""``corpus-kb operations`` — the machine-readable operation inventory.

Emits one JSON document describing EVERY operation in the catalogue
(``corpus_kb.contracts``): surfaces, parameters with defaults, result
envelope keys, the error taxonomy with exit codes, and per-operation CLI
exit codes where the CLI documents them. ``--openapi`` swaps the payload
for the OpenAPI 3.1 document of the HTTP surface, generated from the same
rows. Default output is pretty-printed; ``--indent 0`` is one compact line.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from corpus_kb.contracts import OPERATIONS, SKIPPED_OPERATIONS
from corpus_kb.contracts.model import ERROR_MAP, HTTP, Operation, SurfaceSpec


def _surface_json(surface: SurfaceSpec) -> dict[str, Any]:
    """One surface row as plain JSON (kind, route, method)."""
    out: dict[str, Any] = {"kind": surface.kind, "route": surface.route}
    if surface.kind == HTTP:
        out["method"] = surface.method
    return out


def _operation_json(op: Operation) -> dict[str, Any]:
    """One catalogue row as plain JSON for the inventory document."""
    return {
        "name": op.name,
        "family": op.family,
        "description": op.description,
        "handler": op.handler,
        "parity": "tested",
        "surfaces": [_surface_json(s) for s in op.surfaces],
        "params": [
            {"name": p.name, "required": p.required, "default": p.default} for p in op.params
        ],
        "envelope_keys": list(op.envelope_keys),
        "allowed_extra": {
            "http": sorted(op.http_extra),
            "socket": sorted(op.socket_extra),
        },
        "exit_codes": list(op.exit_codes) if op.exit_codes else None,
    }


def build_inventory() -> dict[str, Any]:
    """Assemble the inventory document from the catalogue rows."""
    return {
        "service": "corpus-kb",
        "generated_from": "corpus_kb.contracts",
        "operations": [_operation_json(op) for op in OPERATIONS],
        "skipped": [
            {
                "name": skip.name,
                "family": skip.family,
                "parity": "skipped",
                "reason": skip.reason,
                "surfaces": [_surface_json(s) for s in skip.surfaces],
                "exit_codes": list(skip.exit_codes) if skip.exit_codes else None,
            }
            for skip in SKIPPED_OPERATIONS
        ],
        "error_map": {
            "description": (
                "One failure class maps to one HTTP status, one socket "
                "code, and one CLI exit code (corpus_kb.errors taxonomy)."
            ),
            "types": {
                error_type: {
                    "http_status": triplet[0],
                    "socket_code": triplet[1],
                    "cli_exit": triplet[2],
                }
                for error_type, triplet in ERROR_MAP.items()
            },
        },
    }


def _emit(payload: dict[str, Any], indent: int) -> None:
    """Print the JSON document (``--indent 0`` gives one compact line)."""
    if indent == 0:
        typer.echo(json.dumps(payload, separators=(",", ":")))
    else:
        typer.echo(json.dumps(payload, indent=indent))


def operations(
    indent: int = typer.Option(2, "--indent", help="JSON indent; 0 gives one compact line"),
    openapi: bool = typer.Option(
        False, "--openapi", help="Emit the OpenAPI 3.1 document for the HTTP surface"
    ),
) -> int:
    """Print the machine-readable inventory of every Corpus-KB operation."""
    from corpus_kb.contracts.openapi import build_openapi

    payload: dict[str, Any] = build_openapi(OPERATIONS) if openapi else build_inventory()
    _emit(payload, indent)
    return 0


def register(app: typer.Typer) -> None:
    """Attach the ``operations`` command to the main Typer app."""
    app.command("operations")(operations)
