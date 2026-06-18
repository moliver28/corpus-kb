"""MCP tools for ingesting documents and files into the RAG system.

Tools:
- ingest_file: Ingest a single file (auto-detects type)
- ingest_text: Ingest raw text with optional type hint
- ingest_directory: Ingest all supported files in a directory
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from storage.lancedb_store import LanceDBStore
from storage.duckdb_engine import DuckDBEngine
from storage.graph_store import GraphStore
from chunking.detector import FileTypeDetector, detect_file_type
from chunking.hierarchy import HierarchyResolver
from rag.embedder import OllamaEmbedder
from utils.models import Chunk, Document


def _ingest_single_file(
    file_path: str,
    detector: FileTypeDetector,
    embedder: OllamaEmbedder,
    store: LanceDBStore,
    graph: GraphStore,
    resolver: HierarchyResolver,
    database: Optional[DuckDBEngine] = None,
) -> dict:
    """Ingest a single file: detect type → chunk → embed → store → graph."""
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File not found: {file_path}")

    content = path.read_text(encoding="utf-8", errors="replace")
    file_type = detect_file_type(file_path, content)
    chunker = detector.get_chunker(file_type)

    # Chunk
    raw_chunks = chunker.chunk(content, file_path=str(path))

    # Resolve hierarchy
    chunks = resolver.resolve(raw_chunks)

    # Create document record with extracted metadata
    stat = path.stat()
    doc = Document(
        source=str(path),
        source_type=file_type,
        metadata={
            "size_bytes": stat.st_size,
            "language": file_type,
        },
        chunk_count=len(chunks),
    )

    # Embed + store
    chunks = embedder.embed_chunks(chunks)
    for chunk in chunks:
        chunk.doc_id = doc.doc_id
        chunk.source = doc.source

    store.insert_document(doc)
    store.insert_chunks(chunks)

    # Graph: create entity nodes for code entities
    for chunk in chunks:
        if chunk.entity_name:
            graph.add_entity(
                name=chunk.entity_name,
                type=chunk.chunk_type,
                metadata={
                    "chunk_id": chunk.chunk_id,
                    "file_path": chunk.file_path,
                    "start_line": chunk.start_line,
                },
            )

    # Auto-populate relational database
    if database is not None:
        try:
            database.sync_from_lancedb(store)
        except Exception:
            pass  # Non-blocking

    return {
        "doc_id": doc.doc_id,
        "source": doc.source,
        "source_type": file_type,
        "chunk_count": len(chunks),
        "file_path": str(path),
    }


def _ingest_text(
    text: str,
    source: str,
    file_type: Optional[str],
    detector: FileTypeDetector,
    embedder: OllamaEmbedder,
    store: LanceDBStore,
    graph: GraphStore,
    resolver: HierarchyResolver,
    database: Optional[DuckDBEngine] = None,
) -> dict:
    """Ingest raw text."""
    if not file_type:
        # Auto-detect
        if text.strip().startswith("#") or text.strip().startswith("##"):
            file_type = "markdown"
        elif text.startswith("#!"):
            file_type = "code"
        else:
            file_type = "text"

    chunker = detector.get_chunker(file_type)
    raw_chunks = chunker.chunk(text, file_path=source)
    chunks = resolver.resolve(raw_chunks)

    doc = Document(
        source=source,
        source_type=file_type,
        metadata={"language": file_type},
        chunk_count=len(chunks),
    )

    chunks = embedder.embed_chunks(chunks)
    for chunk in chunks:
        chunk.doc_id = doc.doc_id
        chunk.source = doc.source

    store.insert_document(doc)
    store.insert_chunks(chunks)

    # Auto-populate relational database
    if database is not None:
        try:
            database.sync_from_lancedb(store)
        except Exception:
            pass  # Non-blocking

    return {
        "doc_id": doc.doc_id,
        "source": doc.source,
        "source_type": file_type,
        "chunk_count": len(chunks),
    }


def register_tools(
    mcp,
    detector: FileTypeDetector,
    embedder: OllamaEmbedder,
    store: LanceDBStore,
    graph: GraphStore,
    resolver: HierarchyResolver,
    database: Optional[DuckDBEngine] = None,
):
    """Register all ingest tools with the MCP server."""

    @mcp.tool()
    def ingest_file(file_path: str) -> dict:
        """Ingest a single file. Auto-detects code/markdown/text."""
        return _ingest_single_file(
            file_path, detector, embedder, store, graph, resolver, database
        )

    @mcp.tool()
    def ingest_text(text: str, source: str = "clipboard",
                    file_type: Optional[str] = None) -> dict:
        """Ingest raw text with optional type hint (code/markdown/text)."""
        return _ingest_text(
            text, source, file_type, detector, embedder, store, graph, resolver, database
        )

    @mcp.tool()
    def ingest_directory(directory_path: str,
                         recursive: bool = True) -> list[dict]:
        """Ingest all supported files in a directory.

        Supports all extensions in CodeChunker.LANGUAGE_MAP plus .md, .rst, .txt.
        """
        dir_path = Path(directory_path)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory_path}")

        # Collect all supported extensions
        from chunking.detector import CODE_EXTENSIONS, MARKDOWN_EXTENSIONS
        supported = set(CODE_EXTENSIONS.keys()) | MARKDOWN_EXTENSIONS | {".txt"}

        results = []
        pattern = "**/*" if recursive else "*"
        for f in dir_path.glob(pattern):
            if f.is_file() and f.suffix.lower() in supported:
                try:
                    result = _ingest_single_file(
                        str(f), detector, embedder, store, graph, resolver, database
                    )
                    results.append(result)
                except Exception as e:
                    results.append({
                        "file_path": str(f),
                        "error": str(e),
                    })

        return results

    @mcp.tool()
    def list_documents() -> list[dict]:
        """List all ingested documents with their metadata."""
        docs = store.list_documents()
        return [
            {
                "doc_id": d["doc_id"],
                "source": d["source"],
                "source_type": d["source_type"],
                "chunk_count": d["chunk_count"],
                "created_at": str(d["created_at"]),
            }
            for d in docs
        ]

    @mcp.tool()
    def delete_document(doc_id: str) -> dict:
        """Delete an ingested document by its doc_id."""
        try:
            store.delete_document(doc_id)
            return {"doc_id": doc_id, "status": "deleted"}
        except KeyError:
            return {"doc_id": doc_id, "status": "not_found"}
        except Exception as e:
            return {"doc_id": doc_id, "status": "error", "message": str(e)}
