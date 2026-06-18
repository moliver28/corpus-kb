"""MCP tools for the persistent relational database layer.

Tools:
  - sql_query:        Full SQL read (SELECT, CTE, JOIN, GROUP BY, window functions)
  - sql_execute:      Parameterized SQL write (INSERT, UPDATE, DELETE) with safety rails
  - sql_tables:       List available tables and their schemas
  - add_tag:          Create a tag
  - tag_document:     Apply a tag to a document
  - untag_document:   Remove a tag from a document
  - get_document_tags: List tags on a document
  - set_metadata:     Set a metadata key-value pair
  - get_metadata:     Retrieve metadata by key and/or document
  - sync_database:    Manually trigger LanceDB → relational sync
"""

from __future__ import annotations

from typing import Optional

from storage.duckdb_engine import DuckDBEngine
from storage.lancedb_store import LanceDBStore


def register_tools(mcp, engine: DuckDBEngine, lancedb_store: LanceDBStore):
    """Register all database tools with the MCP server."""

    @mcp.tool()
    def sql_query(query: str, limit: int = 100) -> dict:
        """Run a SQL SELECT query over relational tables.

        Full SQL supported: SELECT, JOIN, CTE, GROUP BY, window functions,
        subqueries, UNION, etc.

        Available tables:
          - documents:   Document-level metadata (doc_id, source, source_type,
                         chunk_count, file_size, file_hash, language, created_at)
          - chunks:      Individual text chunks (chunk_id, doc_id, chunk_index,
                         source_type, chunk_type, entity_name, file_path,
                         start_line, end_line, char_count)
          - tags:        Defined tags (tag_id, name, color, description)
          - document_tags: Many-to-many mapping (doc_id, tag_id)
          - metadata:    Flexible key-value store (key, value, doc_id)

        Examples:
          SELECT source_type, COUNT(*) as cnt FROM documents GROUP BY source_type
          SELECT * FROM chunks WHERE source_type = 'code' LIMIT 10
          SELECT d.source, COUNT(c.chunk_id) as chunks
            FROM documents d JOIN chunks c ON d.doc_id = c.doc_id
            GROUP BY d.source ORDER BY chunks DESC
          SELECT d.source FROM documents d
            JOIN document_tags dt ON d.doc_id = dt.doc_id
            JOIN tags t ON dt.tag_id = t.tag_id
            WHERE t.name = 'important'

        Args:
            query: SQL SELECT query string.
            limit: Max rows to return (default 100, max 5000).

        Returns:
            Dict with "columns", "rows", and "row_count".
        """
        result = engine.execute(query)
        if result.get("rows") and not result.get("error"):
            result["rows"] = result["rows"][:min(limit, 5000)]
        return result

    @mcp.tool()
    def sql_execute(statement: str) -> dict:
        """Execute a write SQL statement (INSERT, UPDATE, DELETE) with safety rails.

        Safety rules enforced by the engine:
          - DELETE/UPDATE without WHERE clause is BLOCKED
          - DROP TABLE/DATABASE/SCHEMA is BLOCKED

        Use parameterized ? placeholders for values to prevent injection.
        For SELECT queries, use sql_query instead.

        Examples:
          INSERT INTO tags (name, color) VALUES ('urgent', 'red')
          UPDATE documents SET source_type = 'markdown' WHERE doc_id = 'abc-123'
          DELETE FROM document_tags WHERE doc_id = 'abc-123'
          INSERT INTO metadata (key, value, doc_id) VALUES ('reviewer', 'alice', 'abc-123')

        Args:
            statement: SQL write statement (INSERT, UPDATE, DELETE).

        Returns:
            Dict with "affected_rows" count, or "error" if blocked.
        """
        return engine.execute(statement)

    @mcp.tool()
    def sql_tables() -> list[dict]:
        """List all available relational tables with their schema.

        Returns table name, column name, column type, and nullability for
        each column in every user table.
        """
        try:
            tables = engine.conn.execute("""
                SELECT table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'main'
                ORDER BY table_name, ordinal_position
            """).fetchall()
            grouped: dict[str, list[dict]] = {}
            for t in tables:
                name = t[0]
                if name not in grouped:
                    grouped[name] = []
                grouped[name].append({
                    "column": t[1],
                    "type": t[2],
                    "nullable": t[3] == "YES",
                })
            return [{"table": k, "columns": v} for k, v in grouped.items()]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    def add_tag(name: str, color: Optional[str] = None,
                description: Optional[str] = None) -> dict:
        """Create a new tag for categorizing documents."""
        return engine.add_tag(name, color, description)

    @mcp.tool()
    def tag_document(doc_id: str, tag: str) -> dict:
        """Apply a tag to a document. Creates the tag if it doesn't exist."""
        return engine.tag_document(doc_id, tag)

    @mcp.tool()
    def untag_document(doc_id: str, tag: str) -> dict:
        """Remove a tag from a document."""
        return engine.untag_document(doc_id, tag)

    @mcp.tool()
    def get_document_tags(doc_id: str) -> list[dict]:
        """Get all tags applied to a document."""
        return engine.get_document_tags(doc_id)

    @mcp.tool()
    def set_metadata(key: str, value: str, doc_id: Optional[str] = None) -> dict:
        """Set a metadata key-value pair, optionally scoped to a document.

        Overwrites any existing value for the same key+doc_id combination.
        """
        return engine.set_metadata(key, value, doc_id)

    @mcp.tool()
    def get_metadata(key: Optional[str] = None, doc_id: Optional[str] = None) -> list[dict]:
        """Retrieve metadata entries, optionally filtered by key and/or doc_id.

        Omit both to get all metadata. Filter by key to find all values for a key.
        Filter by doc_id to find all metadata for a document.
        """
        return engine.get_metadata(key, doc_id)

    @mcp.tool()
    def sync_database() -> dict:
        """Sync data from LanceDB vector store into relational tables.

        Call this after ingesting documents to make relational queries up to date.
        The sync is idempotent — call it anytime.
        """
        return engine.sync_from_lancedb(lancedb_store)

    @mcp.tool()
    def query_document_stats() -> dict:
        """Get aggregate statistics about the document corpus via SQL.

        Returns: total documents, total chunks, docs by type, chunks by type,
        average chunks per document, date range.
        """
        result = engine.execute("""
            SELECT
                COUNT(DISTINCT d.doc_id) as total_documents,
                COUNT(c.chunk_id) as total_chunks,
                COUNT(DISTINCT d.doc_id) FILTER (WHERE d.source_type = 'code') as code_docs,
                COUNT(DISTINCT d.doc_id) FILTER (WHERE d.source_type = 'markdown') as markdown_docs,
                COUNT(DISTINCT d.doc_id) FILTER (WHERE d.source_type = 'text') as text_docs,
                COUNT(c.chunk_id) FILTER (WHERE c.source_type = 'code') as code_chunks,
                COUNT(c.chunk_id) FILTER (WHERE c.source_type = 'markdown') as markdown_chunks,
                COUNT(c.chunk_id) FILTER (WHERE c.source_type = 'text') as text_chunks,
                ROUND(AVG(c.char_count)) as avg_chunk_chars,
                MIN(d.created_at) as earliest_doc,
                MAX(d.created_at) as latest_doc
            FROM documents d
            LEFT JOIN chunks c ON d.doc_id = c.doc_id
        """)
        return result
