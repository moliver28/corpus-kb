"""MCP ingest tools for Corpus-KB.

All ingest functions are async and require an asyncpg.Pool for Postgres writes.
``source_type`` overrides on ingest_file/ingest_directory name a REGISTERED
INGESTION STRATEGY (explicit resolution precedence); unknown names return a
clear error listing the valid strategies.
"""

from __future__ import annotations

from pathlib import Path

import asyncpg

from ..ingest_watch import WATCH_SUFFIXES
from ..ingestion.registry import strategy_names
from .ingest_common import ingest_text, load_config_or_pass, run_pipeline
from .ingest_surface_tools import ingest_reembed, ingest_status

__all__ = [
    "delete_document",
    "ingest_directory",
    "ingest_file",
    "ingest_reembed",
    "ingest_status",
    "ingest_text",
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
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
    source_type: str | None = None,
) -> dict[str, object]:
    """Ingest a single file (auto-detects type).

    ``source_type``, when given, explicitly pins the ingestion strategy
    (e.g. "transcript"); the DOCUMENT source type is still auto-detected
    from the file extension.
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
    )


async def ingest_directory(
    directory_path: str,
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
    source_type: str | None = None,
) -> dict[str, object]:
    """Ingest all supported files in a directory.

    ``source_type`` optionally pins the ingestion strategy for every file
    (validated once up front against the registry).
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

    for file_path in directory.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in WATCH_SUFFIXES:
            result = await ingest_file(str(file_path), pg_pool, config, tenant_id, source_type)
            results.append(result)
            if result.get("status") == "success":
                total_documents += 1
                raw_count = result.get("entity_count", 0)
                if isinstance(raw_count, int):
                    total_entities += raw_count

    return {
        "status": "success",
        "total_documents": total_documents,
        "total_entities": total_entities,
        "results": results,
    }


async def list_documents(
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    """List all ingested documents."""
    return {"status": "success", "documents": []}


async def delete_document(
    document_id: str,
    pg_pool: asyncpg.Pool,
    config: dict[str, object] | None = None,
) -> dict[str, object]:
    """Delete a document by ID and all related rows."""
    try:
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                "00000000-0000-0000-0000-000000000001",
            )
            await conn.execute(
                "DELETE FROM documents WHERE doc_id = $1",
                document_id,
            )
    except Exception as exc:
        return {"status": "error", "message": f"Delete failed: {exc}"}
    return {"status": "success", "document_id": document_id}


def _detect_source_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".rst"}:
        return "markdown"
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
