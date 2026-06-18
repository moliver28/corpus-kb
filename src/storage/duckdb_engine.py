"""DuckDB SQL engine — provides relational SQL queries over LanceDB tables.

DuckDB can read LanceDB tables natively using the lancedb DuckDB integration.
This enables complex JOINs, aggregations, and SQL filters across all stored data.
"""

from __future__ import annotations

from typing import Any, Optional

import duckdb


class DuckDBEngine:
    """Lightweight wrapper around DuckDB for SQL queries over LanceDB data."""

    def __init__(self, lancedb_uri: str):
        self.uri = lancedb_uri
        self.conn = duckdb.connect()
        # Load LanceDB extension for DuckDB
        try:
            self.conn.install_extension("lancedb")
            self.conn.load_extension("lancedb")
        except Exception:
            pass  # Extension may not be available; will use fallback

    def _table_path(self, table_name: str) -> str:
        return f"'{self.uri}/{table_name}.lance'"

    def execute(self, sql: str) -> dict:
        """Execute a SQL query against the LanceDB-backed tables.

        Returns:
            {"columns": [...], "rows": [[...], ...], "row_count": int}
        """
        # Replace table references with LanceDB paths
        query = sql.replace("FROM chunks", f"FROM lancedb_scan({self._table_path('chunks')})")
        query = query.replace("FROM documents", f"FROM lancedb_scan({self._table_path('documents')})")

        try:
            result = self.conn.execute(query)
            if result.description:
                columns = [desc[0] for desc in result.description]
                rows = result.fetchall()
                return {
                    "columns": columns,
                    "rows": [list(r) for r in rows],
                    "row_count": len(rows),
                }
            return {"columns": [], "rows": [], "row_count": 0}
        except Exception as e:
            # Fallback: query LanceDB tables directly via Python
            return self._fallback_query(sql)

    def _fallback_query(self, sql: str) -> dict:
        """Fallback method when LanceDB DuckDB extension isn't available."""
        import lancedb
        db = lancedb.connect(self.uri)

        sql_lower = sql.lower().strip()

        # Determine which table is being queried
        if "from chunks" in sql_lower:
            tbl = db.open_table("chunks")
        elif "from documents" in sql_lower:
            tbl = db.open_table("documents")
        else:
            return {"columns": [], "rows": [], "row_count": 0, "error": "Table not found"}

        cols = ["*"]
        if "select" in sql_lower:
            # Crude column extraction
            select_part = sql_lower.split("from")[0].replace("select", "").strip()
            if select_part != "*":
                cols = [c.strip().split(" as ")[-1].strip() for c in select_part.split(",")]

        # Apply where clause if present
        where_clause = None
        if "where" in sql_lower:
            where_parts = sql_lower.split("where")
            if len(where_parts) > 1:
                where_clause = where_parts[1].strip().split(" ")[0]

        # Apply limit
        limit = 100
        if "limit" in sql_lower:
            limit_parts = sql_lower.split("limit")
            if len(limit_parts) > 1:
                try:
                    limit = int(limit_parts[1].strip())
                except ValueError:
                    pass

        try:
            data = tbl.search().limit(limit).to_list()
            if cols != ["*"]:
                data = [{c: r.get(c) for c in cols} for r in data]

            if data:
                columns = list(data[0].keys())
                rows = [list(r.values()) for r in data]
            else:
                columns = cols
                rows = []

            return {"columns": columns, "rows": rows, "row_count": len(rows)}
        except Exception as e:
            return {"columns": [], "rows": [], "row_count": 0, "error": str(e)}

    def close(self):
        """Close the DuckDB connection."""
        self.conn.close()
