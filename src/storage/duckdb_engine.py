"""Persistent DuckDB relational database engine.

Provides a full relational SQL layer — file-backed, with defined schemas,
CRUD operations over the single DuckDB store. Provides the
previous in-memory read-only query layer.

Schema:
  - documents:  Relational metadata for SQL joins
  - chunks:     Chunk indices for SQL joins (standalone mode)
  - tags:       Structured tag system (name + color)
  - document_tags: Many-to-many doc-tag mapping
  - metadata:   Flexible key-value store for arbitrary document metadata

Design:
  - Persistent DuckDB file (not :memory:) — survives restarts
  - store maintenance hook (FTS rebuild) on sync_database
  - Full SQL support: SELECT, INSERT, UPDATE, DELETE, CTEs, JOINs
  - Safety rails on write operations (no DROP TABLE, no unbounded DELETE)
  - Hybrid vector+SQL queries via LanceDB's native SQL filtering
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import duckdb


def _resolve_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


class DuckDBEngine:
    """Relational SQL layer (tags, metadata, ad-hoc SQL) over the corpus database.

    Two modes:
      - attached (recommended): pass `store=` to share the DuckDBStore's
        connection. documents/chunks/schema management belongs to the store.
      - standalone: pass storage_path to open its own corpus.db and create
        the legacy relational-only schema (used by unit tests).
    """

    def __init__(self, storage_path: Optional[str] = None, db_name: str = "corpus.db",
                 store=None):
        if store is not None:
            self.store = store
            self.conn = store.conn
            self.storage_path = store.db_path
            self._ensure_aux_schema()
            return
        if storage_path is None:
            raise ValueError("DuckDBEngine needs either storage_path or store=")
        self.storage_path = _resolve_path(storage_path)
        os.makedirs(self.storage_path, exist_ok=True)
        db_path = os.path.join(self.storage_path, db_name)
        self.conn = duckdb.connect(db_path)
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_schema(self):
        """Create relational tables if they don't exist (standalone mode)."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id          TEXT PRIMARY KEY,
                source          TEXT NOT NULL,
                source_type     TEXT NOT NULL DEFAULT 'text',
                chunk_count     INTEGER NOT NULL DEFAULT 0,
                file_size       BIGINT,
                file_hash       TEXT,
                language        TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata_json   TEXT
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id        TEXT PRIMARY KEY,
                doc_id          TEXT NOT NULL REFERENCES documents(doc_id),
                chunk_index     INTEGER NOT NULL DEFAULT 0,
                source_type     TEXT NOT NULL DEFAULT 'text',
                chunk_type      TEXT NOT NULL DEFAULT 'paragraph',
                entity_name     TEXT,
                heading_path    TEXT,
                file_path       TEXT,
                start_line      INTEGER,
                end_line        INTEGER,
                char_count      INTEGER NOT NULL DEFAULT 0,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (doc_id) REFERENCES documents(doc_id)
            )
        """)
        self._ensure_aux_schema()

    def _ensure_aux_schema(self):
        """Tags, document_tags, metadata: needed in both modes."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                tag_id          INTEGER PRIMARY KEY,
                name            TEXT NOT NULL UNIQUE,
                color           TEXT,
                description     TEXT,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS document_tags (
                doc_id          TEXT NOT NULL REFERENCES documents(doc_id),
                tag_id          INTEGER NOT NULL REFERENCES tags(tag_id),
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (doc_id, tag_id)
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                id              INTEGER PRIMARY KEY,
                key             TEXT NOT NULL,
                value           TEXT NOT NULL,
                doc_id          TEXT NOT NULL DEFAULT '',
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (key, doc_id)
            )
        """)
        # Create indexes for common query patterns
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source_type ON chunks(source_type)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_source_type ON documents(source_type)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_document_tags_tag_id ON document_tags(tag_id)")

    # ------------------------------------------------------------------
    # Store maintenance (replaces the old LanceDB sync)
    # ------------------------------------------------------------------

    def sync_from_store(self, store) -> dict:
        """Single-store architecture: data is already in DuckDB.

        Kept as a maintenance hook for tools that called sync_database.
        Rebuilds the FTS index and returns current counts.
        """
        store.ensure_fts_index()
        stats = store.get_stats()
        return {
            "documents_synced": stats.total_documents,
            "chunks_synced": stats.total_chunks,
            "status": "single-store (DuckDB); nothing to mirror",
        }

    # ------------------------------------------------------------------
    # SQL execute (read + write with safety)
    # ------------------------------------------------------------------

    def execute(self, sql: str) -> dict:
        """Execute any SQL query with safety constraints on write operations.

        Safety rules:
          - DROP TABLE / DROP DATABASE / DROP SCHEMA are blocked
          - DELETE without WHERE is blocked
          - UPDATE without WHERE is blocked
          - INSERT ... SELECT is allowed
          - SELECT, CTE, JOIN, GROUP BY, window functions are unrestricted

        Returns:
            {"columns": [...], "rows": [[...], ...], "row_count": int}
            For write queries, returns {"affected_rows": int}
        """
        sql_stripped = sql.strip().upper()

        # Block destructive operations
        for dangerous in ["DROP TABLE", "DROP DATABASE", "DROP SCHEMA"]:
            if dangerous in sql_stripped:
                return {
                    "error": f"Operation '{dangerous}' is blocked for safety. "
                             "Use the dedicated delete tool for document removal.",
                    "columns": [], "rows": [], "row_count": 0,
                }

        # Require WHERE for DELETE/UPDATE
        if sql_stripped.startswith("DELETE") and "WHERE" not in sql_stripped:
            return {
                "error": "DELETE without WHERE clause is blocked. "
                         "Add a WHERE clause to scope the deletion.",
                "columns": [], "rows": [], "row_count": 0,
            }
        if sql_stripped.startswith("UPDATE") and "WHERE" not in sql_stripped:
            return {
                "error": "UPDATE without WHERE clause is blocked. "
                         "Add a WHERE clause to scope the update.",
                "columns": [], "rows": [], "row_count": 0,
            }

        try:
            result = self.conn.execute(sql)

            # Check if this was a write operation (no result set)
            if result.description is None:
                affected = self.conn.execute("SELECT changes()").fetchone()[0]
                return {"affected_rows": affected}

            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
            }
        except Exception as e:
            return {
                "error": str(e),
                "columns": [], "rows": [], "row_count": 0,
            }

    def execute_safe(self, sql: str, params: Optional[list] = None) -> dict:
        """Execute a parameterized SQL statement (for INSERT/UPDATE/DELETE).

        Uses parameterized queries to prevent SQL injection.
        """
        sql_stripped = sql.strip().upper()
        for dangerous in ["DROP", "ALTER", "CREATE"]:
            if sql_stripped.startswith(dangerous) and "TABLE IF NOT EXISTS" not in sql_stripped:
                if dangerous not in ("CREATE",) or "TABLE" not in sql_stripped:
                    pass  # Allow CREATE TABLE IF NOT EXISTS

        try:
            if params:
                result = self.conn.execute(sql, params)
            else:
                result = self.conn.execute(sql)

            if result.description is None:
                affected = self.conn.execute("SELECT changes()").fetchone()[0]
                return {"affected_rows": affected}

            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return {
                "columns": columns,
                "rows": [list(r) for r in rows],
                "row_count": len(rows),
            }
        except Exception as e:
            return {"error": str(e), "affected_rows": 0}

    # ------------------------------------------------------------------
    # Tag management helpers
    # ------------------------------------------------------------------

    def add_tag(self, name: str, color: Optional[str] = None,
                description: Optional[str] = None) -> dict:
        """Create a tag. Returns tag info."""
        try:
            self.conn.execute(
                "INSERT INTO tags (name, color, description) VALUES (?, ?, ?)",
                [name, color, description],
            )
            row = self.conn.execute(
                "SELECT tag_id, name, color, description, created_at FROM tags WHERE name = ?",
                [name],
            ).fetchone()
            return {
                "tag_id": row[0], "name": row[1], "color": row[2],
                "description": row[3], "created_at": str(row[4]),
            }
        except Exception as e:
            return {"error": str(e)}

    def tag_document(self, doc_id: str, tag_name: str) -> dict:
        """Apply a tag to a document. Creates the tag if it doesn't exist."""
        try:
            # Ensure tag exists
            existing = self.conn.execute(
                "SELECT tag_id FROM tags WHERE name = ?", [tag_name]
            ).fetchone()
            if existing:
                tag_id = existing[0]
            else:
                result = self.add_tag(tag_name)
                tag_id = result["tag_id"]

            self.conn.execute(
                "INSERT OR IGNORE INTO document_tags (doc_id, tag_id) VALUES (?, ?)",
                [doc_id, tag_id],
            )
            return {"doc_id": doc_id, "tag": tag_name, "status": "tagged"}
        except Exception as e:
            return {"error": str(e)}

    def untag_document(self, doc_id: str, tag_name: str) -> dict:
        """Remove a tag from a document."""
        try:
            tag = self.conn.execute(
                "SELECT tag_id FROM tags WHERE name = ?", [tag_name]
            ).fetchone()
            if not tag:
                return {"error": f"Tag '{tag_name}' not found"}
            self.conn.execute(
                "DELETE FROM document_tags WHERE doc_id = ? AND tag_id = ?",
                [doc_id, tag[0]],
            )
            return {"doc_id": doc_id, "tag": tag_name, "status": "untagged"}
        except Exception as e:
            return {"error": str(e)}

    def get_document_tags(self, doc_id: str) -> list[dict]:
        """Get all tags for a document."""
        rows = self.conn.execute("""
            SELECT t.tag_id, t.name, t.color, t.description
            FROM tags t
            JOIN document_tags dt ON t.tag_id = dt.tag_id
            WHERE dt.doc_id = ?
        """, [doc_id]).fetchall()
        return [
            {"tag_id": r[0], "name": r[1], "color": r[2], "description": r[3]}
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Metadata helpers
    # ------------------------------------------------------------------

    def set_metadata(self, key: str, value: str, doc_id: Optional[str] = None) -> dict:
        """Set a metadata key-value pair, optionally scoped to a document."""
        try:
            self.conn.execute("""
                INSERT OR REPLACE INTO metadata (key, value, doc_id)
                VALUES (?, ?, ?)
            """, [key, str(value), doc_id])
            return {"key": key, "value": value, "doc_id": doc_id}
        except Exception as e:
            return {"error": str(e)}

    def get_metadata(self, key: Optional[str] = None, doc_id: Optional[str] = None) -> list[dict]:
        """Get metadata, optionally filtered by key and/or doc_id."""
        if key and doc_id:
            rows = self.conn.execute(
                "SELECT key, value, doc_id FROM metadata WHERE key = ? AND doc_id = ?",
                [key, doc_id],
            ).fetchall()
        elif key:
            rows = self.conn.execute(
                "SELECT key, value, doc_id FROM metadata WHERE key = ?", [key]
            ).fetchall()
        elif doc_id:
            rows = self.conn.execute(
                "SELECT key, value, doc_id FROM metadata WHERE doc_id = ?", [doc_id]
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT key, value, doc_id FROM metadata"
            ).fetchall()
        return [
            {"key": r[0], "value": r[1], "doc_id": r[2]} for r in rows
        ]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self):
        """Close the DuckDB connection."""
        try:
            self.conn.close()
        except Exception:
            pass
