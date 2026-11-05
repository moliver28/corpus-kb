"""The ``corpus-kb`` tags/metadata front door (reads: issue #182; writes: Phase-2 Wave 2).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb get-document-tags ID`` — one document's tags.
  * ``corpus-kb get-metadata``         — stored metadata rows, filtered
    by ``--key`` and/or ``--doc-id``.
  * ``corpus-kb add-tag NAME``         — create one tag in the tag
    vocabulary.
  * ``corpus-kb tag-document ID TAG``  — apply one tag to a document.
  * ``corpus-kb untag-document ID TAG`` — remove one tag from a document.
  * ``corpus-kb set-metadata KEY VALUE`` — upsert one metadata key.

Each command calls the SAME shared tool function the MCP + HTTP + socket
surfaces call (``tools.tag_tools``), so every surface returns the same
envelope.

HONEST EXIT CODES (stable for scripting, documented in each command's
help): ``0`` the command answered (an empty list is still 0; an
``already_exists`` tag is still 0 — the tag vocabulary has the name),
``1`` the command failed (an untag of a tag the document does not carry
fails with a typed error), ``3`` the database is unreachable (the shared
ingest plumbing maps connection failures there). Every outcome is PRINTED
or emitted as ``--json``.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli._plumbing import EXIT_FAILED, EXIT_OK
from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync

# Honest write outcomes: the write answered, there is nothing to fix.
_WRITE_OK = ("success", "already_exists")


def register(app: typer.Typer) -> None:
    """Attach the tags/metadata read and write commands to the main Typer app."""
    app.command("get-document-tags")(get_document_tags)
    app.command("get-metadata")(get_metadata)
    app.command("add-tag")(add_tag)
    app.command("tag-document")(tag_document)
    app.command("untag-document")(untag_document)
    app.command("set-metadata")(set_metadata)


def _emit_rows(result: dict[str, object], json_output: bool, label: str) -> None:
    """Print the plain result rows, or the exact envelope with --json."""
    if json_output:
        # The EXACT tool envelope — no reshaping, no added keys.
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status != "success":
        print(f"{label}: {status}")
        return
    rows = result.get("result")
    dicts = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    for row in dicts:
        print(json.dumps(row, default=str))
    if not dicts:
        print("no results")


def _emit_write(result: dict[str, object], json_output: bool, label: str, done: str) -> None:
    """Print the write outcome, or the exact envelope with --json."""
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status not in _WRITE_OK:
        print(f"{label}: {status}")
        message = result.get("message")
        if message:
            print(f"  {message}")
        return
    if status == "already_exists":
        print(f"tag {result.get('name')} already exists")
    elif "tag_id" in result:
        print(f"tag {result.get('name')} created")
    elif "tag" in result:
        print(f"tag {result.get('tag')} {done} {result.get('doc_id')}")
    else:
        print(f"metadata {result.get('key')} saved")


async def _write_flow(
    label: str,
    run: Callable[[Any], Awaitable[dict[str, object]]],
    done: str,
    json_output: bool,
) -> int:
    """One write over the configured database, one honest exit code."""
    from corpus_kb.errors import CorpusError

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await run(pool)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_write(result, json_output, label=label, done=done)
    return EXIT_OK if str(result.get("status")) in _WRITE_OK else EXIT_FAILED


async def _get_document_tags_flow(doc_id: str, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.tag_tools import get_document_tags as tags_tool

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await tags_tool(pool, doc_id)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_output, label="get-document-tags")
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _get_metadata_flow(key: str | None, doc_id: str | None, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.tag_tools import get_metadata as metadata_tool

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await metadata_tool(pool, key=key, doc_id=doc_id)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_rows(result, json_output, label="get-metadata")
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


def get_document_tags(
    doc_id: str = typer.Argument(..., help="The document UUID to read tags for"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List one document's tags (name, color, description).

    Talks to the configured database directly; no server needed. Exit
    codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _get_document_tags_flow(doc_id, json_output))


def get_metadata(
    key: str | None = typer.Option(None, "--key", help="Only rows with this metadata key"),
    doc_id: str | None = typer.Option(None, "--doc-id", help="Only rows for this document UUID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Read stored metadata rows (key, value, doc_id).

    Talks to the configured database directly; no server needed. Exit
    codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _get_metadata_flow(key, doc_id, json_output))


def add_tag(
    name: str = typer.Argument(..., help="The tag name to create"),
    color: str | None = typer.Option(None, "--color", help="Hex color word, like #112233"),
    description: str | None = typer.Option(None, "--description", help="One line about the tag"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Create one tag in the tag vocabulary.

    A name the vocabulary already has is still exit 0 (the answer names
    it ``already_exists``). Exit codes: 0 answered, 1 failed, 3 the
    database is unreachable.
    """
    from corpus_kb.tools.tag_tools import add_tag as tool

    return _run_sync(
        lambda: _write_flow(
            "add-tag",
            lambda pool: tool(pool, name, color=color, description=description),
            "created",
            json_output,
        )
    )


def tag_document(
    doc_id: str = typer.Argument(..., help="The document UUID to tag"),
    tag: str = typer.Argument(..., help="The tag name to apply"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Apply one tag to a document; a missing tag is created first.

    Exit codes: 0 answered, 1 failed, 3 the database is unreachable.
    """
    from corpus_kb.tools.tag_tools import tag_document as tool

    return _run_sync(
        lambda: _write_flow(
            "tag-document",
            lambda pool: tool(pool, doc_id, tag),
            "applied to",
            json_output,
        )
    )


def untag_document(
    doc_id: str = typer.Argument(..., help="The document UUID to untag"),
    tag: str = typer.Argument(..., help="The tag name to remove"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Remove one tag from a document.

    A tag the document does not carry is a typed error (exit 1) — the
    command never answers a fake success. Exit codes: 0 answered, 1
    failed, 3 the database is unreachable.
    """
    from corpus_kb.tools.tag_tools import untag_document as tool

    return _run_sync(
        lambda: _write_flow(
            "untag-document",
            lambda pool: tool(pool, doc_id, tag),
            "removed from",
            json_output,
        )
    )


def set_metadata(
    key: str = typer.Argument(..., help="The metadata key to set"),
    value: str = typer.Argument(..., help="The value to store"),
    doc_id: str | None = typer.Option(None, "--doc-id", help="Scope the key to this document UUID"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Set one metadata key; the write is an upsert.

    Exit codes: 0 answered, 1 failed, 3 the database is unreachable.
    """
    from corpus_kb.tools.tag_tools import set_metadata as tool

    return _run_sync(
        lambda: _write_flow(
            "set-metadata",
            lambda pool: tool(pool, key, value, doc_id=doc_id),
            "saved",
            json_output,
        )
    )
