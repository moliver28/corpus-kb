"""MCP ingest tools for Corpus-KB.

5 tools:
- ingest_file: Ingest a single file (auto-detects type)
- ingest_text: Ingest raw text with optional type hint
- ingest_directory: Ingest all supported files in a directory
- list_documents: List all ingested documents
- delete_document: Delete a document by ID
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from src.config import load_config
from src.graph.extractor import extract_entities
from src.storage.graph_store import create_graph_store
from src.utils.models import Document


# ============================================================================
# Ingest Functions
# ============================================================================


def _ingest_code(
    text: str,
    document_id: str,
    graph_store: Any,
    config: dict[str, Any],
) -> dict[str, str]:
    """Ingest code and extract entities via AST.

    Args:
        text: Code text.
        document_id: Document ID.
        graph_store: GraphStore instance.
        config: Configuration dict.

    Returns:
        Dict mapping entity names to entity IDs.
    """
    entity_map: dict[str, str] = {}

    # For now, code chunking is handled by the chunking layer
    # which populates chunk.entity_name from AST parsing.
    # This function is a placeholder for future code-specific entity extraction.

    return entity_map


def _ingest_text(
    text: str,
    document_id: str,
    source_type: str,
    graph_store: Any,
    config: dict[str, Any],
) -> dict[str, str]:
    """Ingest markdown/text and extract entities.

    Args:
        text: Text or markdown content.
        document_id: Document ID.
        source_type: "markdown" | "text"
        graph_store: GraphStore instance.
        config: Configuration dict.

    Returns:
        Dict mapping entity names to entity IDs.
    """
    entity_map: dict[str, str] = {}

    # Check if entity extraction is enabled in config
    if not config.get("graph", {}).get("extract_entities", True):
        return entity_map

    # Extract entities from text
    try:
        entities = extract_entities(
            text=text,
            source_type=source_type,
            source_document_id=document_id,
        )
    except Exception as e:
        # Log error but don't fail the ingest
        print(f"Error extracting entities: {e}")
        return entity_map

    # Add entities to graph store
    for entity in entities:
        try:
            entity_id = graph_store.add_entity(entity)
            entity_map[entity.name] = entity_id
        except Exception as e:
            # Log error but continue with other entities
            print(f"Error adding entity {entity.name}: {e}")

    return entity_map


def ingest_file(
    file_path: str | Path,
    graph_store: Optional[Any] = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Ingest a single file (auto-detects type).

    Args:
        file_path: Path to file.
        graph_store: GraphStore instance (optional, created if not provided).
        config: Configuration dict (optional, loaded if not provided).

    Returns:
        Dict with document_id, chunk_count, entity_count, and status.
    """
    if config is None:
        config = load_config()

    if graph_store is None:
        graph_db_path = config.get("storage", {}).get(
            "graph_db", "~/.corpus-kb/graph.db"
        )
        graph_backend = config.get("graph", {}).get("backend", "sqlite")
        graph_store = create_graph_store(graph_backend, graph_db_path)

    file_path = Path(file_path)
    if not file_path.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}

    # Read file
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"status": "error", "message": f"Error reading file: {e}"}

    # Detect source type (simplified; real implementation uses detector.py)
    suffix = file_path.suffix.lower()
    if suffix in {".md", ".markdown", ".rst"}:
        source_type = "markdown"
    elif suffix in {
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
        source_type = "code"
    else:
        source_type = "text"

    # Create document
    document = Document(
        path=str(file_path),
        source_type=source_type,
        content=text,
        size_bytes=len(text.encode("utf-8")),
    )

    # Ingest based on type
    entity_count = 0
    if source_type == "code":
        entity_map = _ingest_code(text, document.document_id, graph_store, config)
        entity_count = len(entity_map)
    else:  # markdown or text
        entity_map = _ingest_text(
            text, document.document_id, source_type, graph_store, config
        )
        entity_count = len(entity_map)

    return {
        "status": "success",
        "document_id": document.document_id,
        "path": str(file_path),
        "source_type": source_type,
        "size_bytes": document.size_bytes,
        "entity_count": entity_count,
        "entities": entity_map,
    }


def ingest_text(
    text: str,
    source_type: str = "text",
    graph_store: Optional[Any] = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Ingest raw text with optional type hint.

    Args:
        text: Raw text content.
        source_type: "code" | "markdown" | "text" (default: "text")
        graph_store: GraphStore instance (optional, created if not provided).
        config: Configuration dict (optional, loaded if not provided).

    Returns:
        Dict with document_id, entity_count, and status.
    """
    if config is None:
        config = load_config()

    if graph_store is None:
        graph_db_path = config.get("storage", {}).get(
            "graph_db", "~/.corpus-kb/graph.db"
        )
        graph_backend = config.get("graph", {}).get("backend", "sqlite")
        graph_store = create_graph_store(graph_backend, graph_db_path)

    # Validate source_type
    if source_type not in {"code", "markdown", "text"}:
        return {"status": "error", "message": f"Invalid source_type: {source_type}"}

    # Create document
    document = Document(
        path="raw_text",
        source_type=source_type,
        content=text,
        size_bytes=len(text.encode("utf-8")),
    )

    # Ingest based on type
    entity_count = 0
    if source_type == "code":
        entity_map = _ingest_code(text, document.document_id, graph_store, config)
        entity_count = len(entity_map)
    else:  # markdown or text
        entity_map = _ingest_text(
            text, document.document_id, source_type, graph_store, config
        )
        entity_count = len(entity_map)

    return {
        "status": "success",
        "document_id": document.document_id,
        "source_type": source_type,
        "size_bytes": document.size_bytes,
        "entity_count": entity_count,
        "entities": entity_map,
    }


def ingest_directory(
    directory_path: str,
    graph_store: Optional[Any] = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Ingest all supported files in a directory.

    Args:
        directory_path: Path to directory.
        graph_store: GraphStore instance (optional, created if not provided).
        config: Configuration dict (optional, loaded if not provided).

    Returns:
        Dict with total_documents, total_entities, and per-file results.
    """
    if config is None:
        config = load_config()

    if graph_store is None:
        graph_db_path = config.get("storage", {}).get(
            "graph_db", "~/.corpus-kb/graph.db"
        )
        graph_backend = config.get("graph", {}).get("backend", "sqlite")
        graph_store = create_graph_store(graph_backend, graph_db_path)

    directory = Path(directory_path)
    if not directory.is_dir():
        return {"status": "error", "message": f"Directory not found: {directory_path}"}

    # Supported extensions
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

    results = []
    total_documents = 0
    total_entities = 0

    for file_path in directory.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in supported_extensions:
            result = ingest_file(str(file_path), graph_store, config)
            results.append(result)
            if result.get("status") == "success":
                total_documents += 1
                total_entities += result.get("entity_count", 0)

    return {
        "status": "success",
        "total_documents": total_documents,
        "total_entities": total_entities,
        "results": results,
    }


def list_documents(
    graph_store: Optional[Any] = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """List all ingested documents.

    Returns:
        Dict with list of documents (placeholder implementation).
    """
    # Placeholder: real implementation would query storage layer
    return {"status": "success", "documents": []}


def delete_document(
    document_id: str,
    graph_store: Optional[Any] = None,
    config: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Delete a document by ID.

    Args:
        document_id: Document ID to delete.

    Returns:
        Dict with status.
    """
    # Placeholder: real implementation would delete from storage layer
    return {"status": "success", "document_id": document_id}
