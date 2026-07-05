"""MCP ingest tools for Corpus-KB."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.storage.graph_store import GraphStore
from src.tools.ingest_common import (
    graph_store_from_config,
    load_config_or_pass,
    run_pipeline,
)


def ingest_file(
    file_path: str,
    graph_store: Optional[GraphStore] = None,
    config: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    """Ingest a single file (auto-detects type)."""
    config = load_config_or_pass(config)
    if graph_store is None:
        graph_store = graph_store_from_config(config)
    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "message": f"File not found: {path}"}
    text = path.read_text(encoding="utf-8")
    return run_pipeline(text, _detect_source_type(path), str(path), config, graph_store)


def ingest_text(
    text: str,
    source_type: str = "text",
    graph_store: Optional[GraphStore] = None,
    config: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    """Ingest raw text with optional type hint."""
    if source_type not in {"code", "markdown", "text"}:
        return {"status": "error", "message": f"Invalid source_type: {source_type}"}
    config = load_config_or_pass(config)
    if graph_store is None:
        graph_store = graph_store_from_config(config)
    return run_pipeline(text, source_type, "raw_text", config, graph_store)


def ingest_directory(
    directory_path: str,
    graph_store: Optional[GraphStore] = None,
    config: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    """Ingest all supported files in a directory."""
    config = load_config_or_pass(config)
    if graph_store is None:
        graph_store = graph_store_from_config(config)
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
            result = ingest_file(str(file_path), graph_store, config)
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


def list_documents(
    graph_store: Optional[GraphStore] = None,
    config: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    """List all ingested documents (placeholder)."""
    return {"status": "success", "documents": []}


def delete_document(
    document_id: str,
    graph_store: Optional[GraphStore] = None,
    config: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    """Delete a document by ID (placeholder)."""
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
