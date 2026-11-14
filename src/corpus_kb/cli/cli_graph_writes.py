"""The ``corpus-kb`` graph update/delete front door (Phase-2 Wave 8).

Split out of ``cli_graph.py`` for the 250-line rule (the catalogue's
``catalogue_graph_writes`` pattern):

  * ``corpus-kb update-entity ID``    — one entity's name and type.
  * ``corpus-kb delete-entity ID``    — one entity plus linked relations.
  * ``corpus-kb update-relation ID``  — one relation's type and weight.
  * ``corpus-kb delete-relation ID``  — one relation row.

Each command calls the SAME shared tool function the MCP + HTTP + socket
surfaces call (``tools.graph_tools``), so every surface returns the same
envelope. The deletes are HARD (no undo): the UI gates them; the CLI
answers the typed envelope honestly.

HONEST EXIT CODES (stable for scripting, documented in each command's
help): ``0`` the command answered (a delete of an unknown ID is still 0
— the delete is idempotent by contract), ``1`` the command failed, ``3``
the database is unreachable.
"""

from __future__ import annotations

import json

import typer

from corpus_kb.cli._plumbing import EXIT_FAILED, EXIT_OK
from corpus_kb.cli.cli_ingest import _load_config, _open_pool, _run_sync


def register(app: typer.Typer) -> None:
    """Attach the four graph write commands to the main Typer app."""
    app.command("update-entity")(update_entity)
    app.command("delete-entity")(delete_entity)
    app.command("update-relation")(update_relation)
    app.command("delete-relation")(delete_relation)


def _emit(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        # The EXACT tool envelope — no reshaping, no added keys.
        print(json.dumps(result, indent=2, default=str))
        return
    status = str(result.get("status", "error"))
    if status != "success":
        print(f"graph: {status}")
        message = result.get("message")
        if message:
            print(f"  {message}")
        return
    if result.get("relations_removed") is not None:
        count = result.get("relations_removed")
        print(f"entity {result.get('entity_id')} deleted ({count} relations removed)")
    elif result.get("deleted") is not None:
        print(f"relation {result.get('relation_id')} deleted")
    elif result.get("relation_id") is not None:
        print(f"relation {result.get('relation_id')} updated ({result.get('relation_type')})")
    elif result.get("entity_id") is not None:
        print(f"entity {result.get('entity_id')} updated ({result.get('entity_type')})")


async def _update_entity_flow(
    entity_id: str,
    name: str | None,
    entity_type: str | None,
    json_output: bool,
) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_tools import update_entity

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await update_entity(
            pool, entity_id, config=cfg, name=name, entity_type=entity_type
        )
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _delete_entity_flow(entity_id: str, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_tools import delete_entity

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await delete_entity(pool, entity_id, config=cfg)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _update_relation_flow(
    relation_id: str,
    relation_type: str | None,
    weight: float | None,
    json_output: bool,
) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_tools import update_relation

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await update_relation(
            pool, relation_id, config=cfg, relation_type=relation_type, weight=weight
        )
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _delete_relation_flow(relation_id: str, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_tools import delete_relation

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await delete_relation(pool, relation_id, config=cfg)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


def update_entity(
    entity_id: str = typer.Argument(..., help="The entity UUID to update"),
    name: str = typer.Option(None, "--name", help="The new name (absent keeps the old one)"),
    entity_type: str = typer.Option(
        None, "--entity-type", help="The new type (absent keeps the old one)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Update one entity's name and type.

    Exit codes: 0 updated, 1 failed (unknown ID or name clash), 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _update_entity_flow(entity_id, name, entity_type, json_output))


def delete_entity(
    entity_id: str = typer.Argument(..., help="The entity UUID to delete"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Delete one entity plus its linked relations (final, no undo).

    Exit codes: 0 deleted (an unknown ID is still 0 — the delete is
    idempotent), 1 failed, 3 the database is unreachable.
    """
    return _run_sync(lambda: _delete_entity_flow(entity_id, json_output))


def update_relation(
    relation_id: str = typer.Argument(..., help="The relation UUID to update"),
    relation_type: str = typer.Option(
        None, "--relation-type", help="The new type (absent keeps the old one)"
    ),
    weight: float = typer.Option(
        None, "--weight", help="The new weight, greater than 0 (absent keeps the old one)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Update one relation's type and weight.

    Exit codes: 0 updated, 1 failed (unknown ID or a weight of 0 or
    less), 3 the database is unreachable.
    """
    return _run_sync(lambda: _update_relation_flow(relation_id, relation_type, weight, json_output))


def delete_relation(
    relation_id: str = typer.Argument(..., help="The relation UUID to delete"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Delete one relation (final, no undo).

    Exit codes: 0 deleted (an unknown ID is still 0 — ``deleted`` says
    false), 1 failed, 3 the database is unreachable.
    """
    return _run_sync(lambda: _delete_relation_flow(relation_id, json_output))
