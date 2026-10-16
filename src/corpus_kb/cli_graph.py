"""The ``corpus-kb`` graph + documents front door (full surface parity).

Top-level commands registered on the main app (see ``register``):

  * ``corpus-kb add-entity NAME``     — one entity into the knowledge graph.
  * ``corpus-kb add-relation A B``    — one relation between two entities.
  * ``corpus-kb list-documents``      — the ingested documents, paginated.
  * ``corpus-kb delete-document ID``  — one document row, idempotent.

Each command calls the SAME shared tool function the MCP + HTTP + socket
surfaces call (``tools.graph_tools`` / ``tools.ingest_tools``), so every
surface returns the same envelope.

HONEST EXIT CODES (stable for scripting, documented in each command's
help): ``0`` the command answered (an empty list is still 0; a delete of
an unknown ID is still 0 — the delete is idempotent by contract), ``1``
the command failed, ``3`` the database is unreachable (the shared ingest
plumbing maps connection failures there). Every outcome is PRINTED or
emitted as ``--json``.
"""

from __future__ import annotations

import json

import typer

# Reuse the ingest front door's shared plumbing (same honest-sync rule);
# cli_ingest imports corpus_kb.cli only lazily, so this import is acyclic.
from corpus_kb.cli_ingest import _load_config, _open_pool, _run_sync

EXIT_OK = 0
EXIT_FAILED = 1


def register(app: typer.Typer) -> None:
    """Attach the four graph/documents commands to the main Typer app."""
    app.command("add-entity")(add_entity)
    app.command("add-relation")(add_relation)
    app.command("list-documents")(list_documents)
    app.command("delete-document")(delete_document)


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
    if result.get("entity_id") is not None:
        print(f"entity {result.get('entity_id')} created ({result.get('entity_type')})")
    elif result.get("relation_id") is not None:
        print(f"relation {result.get('relation_id')} created ({result.get('relation_type')})")
    elif result.get("document_id") is not None:
        print(f"deleted {result.get('document_id')}")


def _emit_documents(result: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, default=str))
        return
    rows = result.get("result")
    docs = [doc for doc in rows if isinstance(doc, dict)] if isinstance(rows, list) else []
    for doc in docs:
        print(
            f"{doc.get('doc_id')}  {doc.get('source_type', ''):10s} "
            f"{doc.get('chunk_count', 0):>4d} chunks  {doc.get('source', '')}"
        )
    if not docs:
        print("no documents")


async def _add_entity_flow(name: str, entity_type: str, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_tools import add_entity

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await add_entity(pool, name, config=cfg, entity_type=entity_type)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _add_relation_flow(
    source: str, target: str, relation_type: str, weight: float, json_output: bool
) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.graph_tools import add_relation

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await add_relation(
            pool,
            source,
            target,
            config=cfg,
            relation_type=relation_type,
            weight=weight,
        )
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _list_documents_flow(limit: int, offset: int, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.ingest_tools import list_documents

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await list_documents(pool, config=cfg, limit=limit, offset=offset)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit_documents(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


async def _delete_document_flow(document_id: str, json_output: bool) -> int:
    from corpus_kb.errors import CorpusError
    from corpus_kb.tools.ingest_tools import delete_document

    cfg = _load_config()
    pool = await _open_pool(cfg)
    try:
        result = await delete_document(document_id, pool, config=cfg)
    except CorpusError as exc:
        print(f"ERROR: {exc}")
        return EXIT_FAILED
    finally:
        await pool.close()
    _emit(result, json_output)
    return EXIT_OK if result.get("status") == "success" else EXIT_FAILED


def add_entity(
    name: str = typer.Argument(..., help="The entity name"),
    entity_type: str = typer.Option("concept", "--entity-type", help="The entity type"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Add one entity to the knowledge graph.

    Exit codes: 0 created, 1 failed, 3 the database is unreachable.
    """
    return _run_sync(lambda: _add_entity_flow(name, entity_type, json_output))


def add_relation(
    source: str = typer.Argument(..., help="The source entity UUID"),
    target: str = typer.Argument(..., help="The target entity UUID"),
    relation_type: str = typer.Option("related_to", "--relation-type", help="The relation type"),
    weight: float = typer.Option(1.0, "--weight", help="The relation weight"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Add one relation between two entities.

    Exit codes: 0 created, 1 failed, 3 the database is unreachable.
    """
    return _run_sync(lambda: _add_relation_flow(source, target, relation_type, weight, json_output))


def list_documents(
    limit: int = typer.Option(100, "--limit", min=1, help="How many documents to show"),
    offset: int = typer.Option(0, "--offset", min=0, help="Skip the first N documents"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """List ingested documents with pagination.

    Exit codes: 0 answered (an empty list is still 0), 1 failed, 3 the
    database is unreachable.
    """
    return _run_sync(lambda: _list_documents_flow(limit, offset, json_output))


def delete_document(
    document_id: str = typer.Argument(..., help="The document UUID to delete"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
) -> int:
    """Delete one document by ID (idempotent; chunks and vectors cascade).

    Exit codes: 0 deleted (an unknown ID is still 0 — the delete is
    idempotent), 1 failed, 3 the database is unreachable.
    """
    return _run_sync(lambda: _delete_document_flow(document_id, json_output))
