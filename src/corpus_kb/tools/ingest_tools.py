"""MCP ingest tools for Corpus-KB.

All ingest functions are async and take the storage seam: an asyncpg.Pool
(Postgres) or any ``SqlGateway`` (e.g. ``DuckDbGateway``) — one pipeline,
both backends. ``source_type`` overrides on ingest_file/ingest_directory/
ingest_url name a REGISTERED INGESTION STRATEGY (explicit resolution
precedence); unknown names return a clear error listing the valid
strategies.

``ingest_url`` lives in ``ingest_url_tools.py`` (250-line soft limit; the
registration helper there is private plumbing) and is re-exported here so the
``corpus_kb.tools.ingest_tools`` import path stays the one-stop tool layer.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import asyncpg

from ..ingest_watch import WATCH_SUFFIXES
from ..ingestion.registry import strategy_names
from ..research.batching import gather_bounded
from ..storage.gateway import SqlGateway
from .ingest_common import (
    DEFAULT_TENANT_ID,
    file_ingest_concurrency,
    ingest_text,
    load_config_or_pass,
    run_pipeline,
)
from .ingest_store import as_gateway, gateway_backend
from .ingest_surface_tools import ingest_reembed, ingest_status
from .ingest_url_tools import ingest_url

__all__ = [
    "delete_document",
    "ingest_directory",
    "ingest_file",
    "ingest_reembed",
    "ingest_status",
    "ingest_text",
    "ingest_url",
    "list_documents",
]


def _validate_strategy_override(source_type: str) -> str | None:
    """Return an error message for an unknown strategy override, else None.

    Private (not an MCP tool): kept out of the frozen tool-contract snapshot.
    """
    if source_type in strategy_names():
        return None
    return (
        f"Unknown source-type strategy '{source_type}'. "
        f"Valid strategies: {', '.join(sorted(strategy_names()))}"
    )


async def ingest_file(
    file_path: str,
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object] | None = None,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
    source_type: str | None = None,
    validate: str | None = None,
) -> dict[str, object]:
    """Ingest a single file (auto-detects type).

    ``source_type``, when given, explicitly pins the ingestion strategy
    (e.g. "transcript"); the DOCUMENT source type is still auto-detected
    from the file extension. ``validate`` is the optional validation gate
    (off | advisory | enforce | calibrate); None falls back to the active
    profile's ``validate`` value, then ``off``.
    """
    config = load_config_or_pass(config)
    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "message": f"File not found: {path}"}
    if source_type is not None:
        error = _validate_strategy_override(source_type)
        if error is not None:
            return {"status": "error", "message": error}
    # Do NOT read the bytes here: run_pipeline(is_file=True) partitions the file
    # via the ingestion strategy and derives its text from the parsed content,
    # so binary formats (PDF, DOCX, ...) work instead of failing on a UTF-8
    # decode.
    return await run_pipeline(
        "",
        _detect_source_type(path),
        str(path),
        config,
        pg_pool,
        tenant_id,
        is_file=True,
        strategy_name=source_type,
        validate_mode=validate,
    )


async def ingest_directory(
    directory_path: str,
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object] | None = None,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
    source_type: str | None = None,
    recursive: bool = True,
    validate: str | None = None,
) -> dict[str, object]:
    """Ingest the supported files in a directory. This is the ONE walk.

    MCP and HTTP route through this function. The CLI front door applies
    the same ``WATCH_SUFFIXES`` filter and calls :func:`ingest_file` per
    file for its per-file output. A file is ingested only when its suffix
    is in ``ingest_watch.WATCH_SUFFIXES`` — the one modality table.

    Envelope (one meaning per key on every surface):

    * ``status``: ``success`` when the walk completed. Per-file failures
      live inside ``results``. ``error`` means a bad path or an unknown
      strategy; nothing was ingested.
    * ``directory``: the requested directory path.
    * ``files_processed``: files attempted (``len(results)``).
    * ``total_documents``: files whose own ingest returned ``success``.
    * ``total_entities``: sum of ``entity_count`` over those successes.
    * ``results``: the per-file envelopes, in walk order.

    ``source_type`` optionally pins the ingestion strategy for every file
    (validated once up front against the registry). ``validate`` is the
    optional validation gate passed to every per-file ingest (off |
    advisory | enforce | calibrate); None falls back to the active
    profile's ``validate`` value, then ``off``.
    """
    config = load_config_or_pass(config)
    directory = Path(directory_path)
    if not directory.is_dir():
        return {"status": "error", "message": f"Directory not found: {directory_path}"}
    if source_type is not None:
        error = _validate_strategy_override(source_type)
        if error is not None:
            return {"status": "error", "message": error}

    results: list[dict[str, object]] = []
    total_documents = 0
    total_entities = 0

    pattern = "**/*" if recursive else "*"
    files = [
        file_path
        for file_path in sorted(directory.glob(pattern))
        if file_path.is_file() and file_path.suffix.lower() in WATCH_SUFFIXES
    ]
    concurrency = file_ingest_concurrency(config, pg_pool)
    results = await gather_bounded(
        files,
        lambda path: ingest_file(str(path), pg_pool, config, tenant_id, source_type, validate),
        concurrency,
    )
    for result in results:
        if result.get("status") == "success":
            total_documents += 1
            raw_count = result.get("entity_count", 0)
            if isinstance(raw_count, int):
                total_entities += raw_count

    return {
        "status": "success",
        "directory": str(directory),
        "files_processed": len(results),
        "total_documents": total_documents,
        "total_entities": total_entities,
        "results": results,
    }


async def list_documents(
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, object]:
    """List ingested documents with pagination.

    Delegates to ``QueryHandler.handle_list_documents`` — the same call
    GET /api/documents and the socket ``list_documents`` method make — so
    all three surfaces return the same envelope:
    ``{"status": "success", "result": [...]}``.
    """
    from ..domain.models import ListDocumentsQuery
    from ..handlers.query_handler import QueryHandler

    query = ListDocumentsQuery(tenant_id=UUID(tenant_id), limit=limit, offset=offset)
    # Built over the caller's storage seam (not the get_query_handler singleton):
    # the singleton pins the first pool it sees, and this tool is handed a
    # pool or gateway explicitly. The query handler takes the engine union,
    # so a DuckDB gateway serves the read the same way a pool does.
    handler = QueryHandler(pg_pool, config=load_config_or_pass(config))
    results = await handler.handle_list_documents(query)
    return {"status": "success", "result": [r.model_dump(mode="json") for r in results]}


async def delete_document(
    document_id: str,
    pg_pool: asyncpg.Pool | SqlGateway,
    config: dict[str, object] | None = None,
    tenant_id: str = DEFAULT_TENANT_ID,
) -> dict[str, object]:
    """Delete a document row by ID. This is the ONE delete implementation.

    MCP and HTTP both route through this function. The delete runs in one
    ``gateway.transaction`` scope (Postgres: tenant GUC set, so row-level
    security sees it). Chunks, vectors, tags, metadata, and research rows
    cascade. Entity and relation rows stay. Their document and chunk links
    become NULL. Event-store rows persist.

    The delete is idempotent: an unknown ID still answers ``success``. The
    event-store audit (``handlers.delete_audit.attach_delete_event``) is
    the command-handler wrapper's additive layer, the same pattern as the
    ingest audit, so audited and unaudited callers share one delete truth.
    """
    is_duckdb = gateway_backend(as_gateway(pg_pool)) == "duckdb"
    try:
        async with as_gateway(pg_pool).transaction(tenant_id) as tx:
            if is_duckdb:
                # No RLS on DuckDB: the tenant predicate IS the isolation.
                await tx.execute(
                    "DELETE FROM documents WHERE doc_id = $1 AND tenant_id = $2",
                    document_id,
                    tenant_id,
                )
            else:
                await tx.execute(
                    "DELETE FROM documents WHERE doc_id = $1",
                    document_id,
                )
    except Exception:
        return {
            "status": "error",
            "message": (
                f"Could not delete document {document_id}. "
                "Check the document ID and tenant, then retry."
            ),
        }
    return {"status": "success", "document_id": document_id}


def _detect_source_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".rst"}:
        return "markdown"
    if suffix == ".pptx":
        return "pptx"
    if suffix in {
        ".py",
        ".js",
        ".ts",
        ".rs",
        ".go",
        ".java",
        ".cpp",
        ".c",
        ".rb",
        ".php",
    }:
        return "code"
    return "text"
