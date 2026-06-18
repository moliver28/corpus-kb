"""MCP tools for SQL queries over the RAG data.

Tool:
- sql_query: Run SQL queries via DuckDB over LanceDB tables
"""

from __future__ import annotations

from storage.duckdb_engine import DuckDBEngine


def register_tools(mcp, engine: DuckDBEngine):
    """Register SQL tools with the MCP server."""

    @mcp.tool()
    def sql_query(query: str, limit: int = 50) -> dict:
        """Run SQL queries over the RAG data using DuckDB.

        Available tables:
          - chunks: All text chunks with embeddings, metadata, and hierarchy
          - documents: Document-level metadata

        Example queries:
          - SELECT chunk_type, COUNT(*) FROM chunks GROUP BY chunk_type
          - SELECT * FROM chunks WHERE source_type = 'code' LIMIT 5
          - SELECT d.source, COUNT(c.chunk_id) as chunk_count
            FROM documents d JOIN chunks c ON d.doc_id = c.doc_id
            GROUP BY d.source

        Args:
            query: SQL query string.
            limit: Max rows to return (max 1000).

        Returns:
            Dict with "columns", "rows", and "row_count".
        """
        result = engine.execute(query)
        if result.get("rows"):
            result["rows"] = result["rows"][:min(limit, 1000)]
        return result
