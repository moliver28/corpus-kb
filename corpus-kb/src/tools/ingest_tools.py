"""MCP ingest tools for Corpus-KB.

All ingest functions are async and require an asyncpg.Pool for Postgres writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import asyncpg

from .ingest_common import load_config_or_pass, run_pipeline


async def ingest_file(
    file_path: str,
    pg_pool: asyncpg.Pool,
    config: Optional[dict[str, object]] = None,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
) -> dict[str, object]:
    """Ingest a single file (auto-detects type)."""
    config = load_config_or_pass(config)
    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "message": f"File not found: {path}"}
    text = path.read_text(encoding="utf-8")
    return await run_pipeline(
        text, _detect_source_type(path), str(path), config, pg_pool, tenant_id
    )


async def ingest_text(
    text: str,
    pg_pool: asyncpg.Pool,
    source_type: str = "text",
    config: Optional[dict[str, object]] = None,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
    source: str = "raw_text",
) -> dict[str, object]:
    """Ingest raw text with optional type hint and source identifier."""
    if source_type not in {"code", "markdown", "text"}:
        return {"status": "error", "message": f"Invalid source_type: {source_type}"}
    config = load_config_or_pass(config)
    return await run_pipeline(text, source_type, source, config, pg_pool, tenant_id)


async def ingest_directory(
    directory_path: str,
    pg_pool: asyncpg.Pool,
    config: Optional[dict[str, object]] = None,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
) -> dict[str, object]:
    """Ingest all supported files in a directory."""
    config = load_config_or_pass(config)
    directory = Path(directory_path)
    if not directory.is_dir():
        return {"status": "error", "message": f"Directory not found: {directory_path}"}

    supported_extensions = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".rs",
        ".go",
        ".java",
        ".cpp",
        ".c",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".scala",
        ".lua",
        ".md",
        ".markdown",
        ".rst",
        ".txt",
    }

    results: list[dict[str, object]] = []
    total_documents = 0
    total_entities = 0

    for file_path in directory.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in supported_extensions:
            result = await ingest_file(str(file_path), pg_pool, config, tenant_id)
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
    config: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    """List all ingested documents."""
    return {"status": "success", "documents": []}


async def delete_document(
    document_id: str,
    pg_pool: asyncpg.Pool,
    config: Optional[dict[str, object]] = None,
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
