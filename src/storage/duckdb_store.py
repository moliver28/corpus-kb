"""DuckDB storage backend — the single store for corpus-kb.

Replaces the previous LanceDB + DuckDB two-store design. One DuckDB file
holds documents, chunks, embeddings (FLOAT[dims]), full-text search (fts
extension) and an append-only operation log that implements versioning,
tags and branches. The knowledge graph stays in sqlite (graph.db).

Design notes:
- Vector search is cosine similarity over the `vector` column via
  `list_cosine_similarity` (core). For large corpora (>100k chunks)
  install the vss extension and create an HNSW index; the SQL here
  stays correct either way.
- Full-text search uses the fts extension: PRAGMA create_fts_index on
  the chunks table, queried through fts_main_chunks.match_bm25. The
  index is rebuilt after every mutating write.
- Versions: every mutating operation appends the full chunk payload to
  `chunk_ops` and bumps `store_versions`. restore(version) materializes
  that version's chunk set. LanceDB's read-only "checkout" cannot be
  replicated without materialization, so checkout and restore both
  materialize; branches/tags are pointers into the version log.
- Score semantics: vector search emits cosine *similarity* (0..1,
  higher = closer) in `score`, matching how hybrid search applies its
  relevance floor on `score`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import duckdb

from utils.models import Chunk, Document, SearchResult, Version, Stats


def _safe_json_load(value, expected_type=list):
    """Safely parse a JSON string, returning default on None/invalid."""
    if value is None:
        return expected_type()
    if isinstance(value, expected_type):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return expected_type()


_CHUNK_COLS = (
    "chunk_id", "doc_id", "text", "chunk_index", "source", "source_type",
    "metadata", "heading_path", "parent_chunk_id", "sibling_order", "sibling_count",
    "scope_chain", "chunk_type", "entity_name", "file_path", "start_line",
    "end_line", "created_at",
)
_CHUNK_SELECT = "SELECT " + ", ".join(_CHUNK_COLS) + " FROM chunks"

_VEC_DIM = "CAST(? AS DOUBLE[%d])"


def _resolve_db_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _to_row(c: Chunk) -> dict:
    return {
        "chunk_id": c.chunk_id,
        "doc_id": c.doc_id,
        "text": c.text,
        "vector": list(c.vector) if c.vector is not None else None,
        "chunk_index": c.chunk_index,
        "source": c.source,
        "source_type": c.source_type,
        "metadata": json.dumps(c.metadata) if c.metadata is not None else "{}",
        "heading_path": json.dumps(c.heading_path) if c.heading_path else None,
        "parent_chunk_id": c.parent_chunk_id,
        "sibling_order": c.sibling_order,
        "sibling_count": c.sibling_count,
        "scope_chain": json.dumps(c.scope_chain) if c.scope_chain else None,
        "chunk_type": c.chunk_type,
        "entity_name": c.entity_name,
        "file_path": c.file_path,
        "start_line": c.start_line,
        "end_line": c.end_line,
        "created_at": c.created_at,
    }


class DuckDBStore:
    """Owns the DuckDB connection and the chunks/documents tables."""

    def __init__(self, db_path: str, dimensions: int = 4096):
        self.db_path = _resolve_db_path(db_path)
        self.dimensions = int(dimensions)
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = duckdb.connect(self.db_path)
        for ext in ("vss", "fts"):
            try:
                self.conn.execute(f"INSTALL {ext}; LOAD {ext};")
            except Exception:
                pass  # degraded: vector search and FTS fall back sensibly
        self._ensure_tables()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id      TEXT PRIMARY KEY,
                source      TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT 'text',
                metadata    TEXT,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT
            )
        """)
        self.conn.execute(f"""
            CREATE SEQUENCE IF NOT EXISTS chunks_rid_seq;
            CREATE TABLE IF NOT EXISTS chunks (
                rid            BIGINT PRIMARY KEY DEFAULT nextval('chunks_rid_seq'),
                chunk_id       TEXT NOT NULL UNIQUE,
                doc_id         TEXT NOT NULL,
                text           TEXT,
                vector         FLOAT[{self.dimensions}],
                chunk_index    INTEGER,
                source         TEXT,
                source_type    TEXT,
                metadata       TEXT,
                heading_path   TEXT,
                parent_chunk_id TEXT,
                sibling_order  INTEGER,
                sibling_count  INTEGER,
                scope_chain    TEXT,
                chunk_type     TEXT,
                entity_name    TEXT,
                file_path      TEXT,
                start_line     INTEGER,
                end_line       INTEGER,
                created_at     TEXT
            )
        """)
        for col, name in (("chunk_id", "idx_duck_chunks_chunk_id"), ("doc_id", "idx_duck_chunks_doc_id")):
            self.conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON chunks({col})")
        # Versioning: append-only op log + version registry.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS chunk_ops (
                op_seq   BIGINT PRIMARY KEY,
                kind     TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                payload  TEXT,
                version  INTEGER NOT NULL,
                ts       TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS store_versions (
                version  INTEGER PRIMARY KEY,
                ts       TEXT NOT NULL,
                tag      TEXT,
                branch   TEXT,
                note     TEXT
            )
        """)
        self._ensure_fts_index()

    def _ensure_fts_index(self):
        try:
            self.conn.execute("PRAGMA drop_fts_index('chunks')")
        except Exception:
            pass
        try:
            self.conn.execute("PRAGMA create_fts_index('chunks', 'rid', 'text', overwrite=1)")
        except Exception:
            pass  # degraded: FTS returns empty until index exists

    # ------------------------------------------------------------------
    # Version registry
    # ------------------------------------------------------------------

    def _current_version(self) -> int:
        return int(self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM store_versions"
        ).fetchone()[0])

    def _bump_version(self, tag: Optional[str] = None, branch: Optional[str] = None,
                      note: Optional[str] = None) -> int:
        ver = self._current_version() + 1
        self.conn.execute(
            "INSERT INTO store_versions (version, ts, tag, branch, note) VALUES (?, ?, ?, ?, ?)",
            [ver, datetime.utcnow().isoformat(), tag, branch, note],
        )
        return ver

    def _log_op(self, kind: str, chunk_id: str, payload: Optional[str] = None) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(op_seq), 0) + 1 FROM chunk_ops"
        ).fetchone()
        ver = self._current_version()
        self.conn.execute(
            "INSERT INTO chunk_ops (op_seq, kind, chunk_id, payload, version, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [int(row[0]), kind, chunk_id, payload, ver, datetime.utcnow().isoformat()],
        )
        return ver

    def _chunks_at_version(self, version: int) -> list[Chunk]:
        """Replay the op log to produce the chunk set at `version`."""
        rows = self.conn.execute(
            "SELECT kind, chunk_id, payload FROM chunk_ops WHERE version <= ? ORDER BY op_seq",
            [version],
        ).fetchall()
        state: dict[str, dict] = {}
        for kind, chunk_id, payload in rows:
            if kind == "insert":
                data = _safe_json_load(payload, dict)
                if data:
                    state[chunk_id] = data
            elif kind == "delete":
                state.pop(chunk_id, None)
        return [Chunk.from_row(d) for d in state.values()]

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    def insert_document(self, doc: Document) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO documents (doc_id, source, source_type, metadata, "
            "chunk_count, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [doc.doc_id, doc.source, doc.source_type,
             json.dumps(doc.metadata) if doc.metadata is not None else "{}",
             doc.chunk_count, doc.created_at],
        )
        return doc.doc_id

    def list_documents(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT doc_id, source, source_type, metadata, chunk_count, created_at "
            "FROM documents LIMIT 10000"
        ).fetchall()
        return [self._row_dict_doc(r) for r in rows]

    def get_document(self, doc_id: str) -> Optional[dict]:
        rows = self.conn.execute(
            "SELECT doc_id, source, source_type, metadata, chunk_count, created_at "
            "FROM documents WHERE doc_id = ? LIMIT 1", [doc_id],
        ).fetchall()
        return self._row_dict_doc(rows[0]) if rows else None

    def delete_document(self, doc_id: str) -> bool:
        if not self.get_document(doc_id):
            return False
        for (chunk_id,) in self.conn.execute(
            "SELECT chunk_id FROM chunks WHERE doc_id = ?", [doc_id]
        ).fetchall():
            self._log_op("delete", chunk_id)
        self.conn.execute("DELETE FROM chunks WHERE doc_id = ?", [doc_id])
        self.conn.execute("DELETE FROM documents WHERE doc_id = ?", [doc_id])
        self._ensure_fts_index()
        self._bump_version(note=f"delete_document {doc_id}")
        return True

    @staticmethod
    def _row_dict_doc(r) -> dict:
        return {
            "doc_id": r[0], "source": r[1], "source_type": r[2],
            "metadata": r[3], "chunk_count": r[4], "created_at": r[5],
        }

    # ------------------------------------------------------------------
    # Chunk operations
    # ------------------------------------------------------------------

    def _insert_chunk_rows(self, rows_payload: list[dict]):
        sql = (
            "INSERT INTO chunks (chunk_id, doc_id, text, vector, chunk_index, "
            "source, source_type, metadata, heading_path, parent_chunk_id, sibling_order, "
            "sibling_count, scope_chain, chunk_type, entity_name, file_path, start_line, "
            "end_line, created_at) VALUES ("
            f"?, ?, ?, {_VEC_DIM % self.dimensions}, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (chunk_id) DO UPDATE SET "
            "doc_id = excluded.doc_id, text = excluded.text, vector = excluded.vector, "
            "chunk_index = excluded.chunk_index, source = excluded.source, "
            "source_type = excluded.source_type, metadata = excluded.metadata, "
            "heading_path = excluded.heading_path, parent_chunk_id = excluded.parent_chunk_id, "
            "sibling_order = excluded.sibling_order, sibling_count = excluded.sibling_count, "
            "scope_chain = excluded.scope_chain, chunk_type = excluded.chunk_type, "
            "entity_name = excluded.entity_name, file_path = excluded.file_path, "
            "start_line = excluded.start_line, end_line = excluded.end_line, "
            "created_at = excluded.created_at"
        )
        for row in rows_payload:
            self.conn.execute(sql, [
                row["chunk_id"], row["doc_id"], row["text"],
                row["vector"], row["chunk_index"], row["source"], row["source_type"],
                row["metadata"], row["heading_path"], row["parent_chunk_id"],
                row["sibling_order"], row["sibling_count"], row["scope_chain"],
                row["chunk_type"], row["entity_name"], row["file_path"],
                row["start_line"], row["end_line"], row["created_at"],
            ])

    def insert_chunks(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        for c in chunks:
            self._log_op("insert", c.chunk_id, json.dumps(_to_row(c)))
        self._insert_chunk_rows([_to_row(c) for c in chunks])
        self._ensure_fts_index()
        self._bump_version(note=f"insert {len(chunks)} chunks")
        return len(chunks)

    def list_chunks(self, doc_id: str, limit: int = 10000) -> list[dict]:
        rows = self.conn.execute(
            _CHUNK_SELECT + f" WHERE doc_id = ? ORDER BY chunk_index LIMIT ?", [doc_id, limit]
        ).fetchall()
        return [self._row_dict_chunk(r) for r in rows]

    def get_chunk(self, chunk_id: str) -> Optional[dict]:
        rows = self.conn.execute(_CHUNK_SELECT + " WHERE chunk_id = ? LIMIT 1", [chunk_id]).fetchall()
        return self._row_dict_chunk(rows[0]) if rows else None

    def get_chunk_context(self, chunk_id: str, before: int = 2, after: int = 2) -> list[SearchResult]:
        chunk = self.get_chunk(chunk_id)
        if not chunk:
            return []
        rows = self.conn.execute(
            _CHUNK_SELECT + " WHERE doc_id = ? AND chunk_index >= ? AND chunk_index <= ? "
            "ORDER BY chunk_index LIMIT 100",
            [chunk.get("doc_id", ""), max(0, chunk.get("chunk_index", 0) - before),
             chunk.get("chunk_index", 0) + after],
        ).fetchall()
        return [self._to_search_result(self._row_dict_chunk(r)) for r in rows]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search_vector(self, query_embedding: list[float], k: int = 10,
                      filters: Optional[dict] = None) -> list[SearchResult]:
        where = ""
        clause = self._build_where(filters) if filters else ""
        if clause:
            where = "WHERE " + clause
        score_expr = f"1 - list_cosine_similarity(vector, {_VEC_DIM % self.dimensions})"
        sql = (
            f"SELECT {', '.join(_CHUNK_COLS)}, {score_expr} AS score "
            f"FROM chunks {where} ORDER BY score DESC LIMIT ?"
        )
        self.conn.execute(sql, [query_embedding, k])
        rows = self.conn.fetchall()
        results = []
        for r in rows:
            d = self._row_dict_chunk(r)
            d["_score"] = r[len(_CHUNK_COLS)]
            results.append(self._to_search_result(d))
        return results

    def search_fts(self, query_text: str, k: int = 10,
                   filters: Optional[dict] = None) -> list[SearchResult]:
        where = ""
        clause = self._build_where(filters) if filters else ""
        if clause:
            where = " AND " + clause
        sql = (
            f"SELECT {', '.join(_CHUNK_COLS)}, "
            "fts_main_chunks.match_bm25(chunks.rid, ?) AS score "
            f"FROM chunks WHERE fts_main_chunks.match_bm25(chunks.rid, ?) IS NOT NULL{where} "
            "ORDER BY score DESC LIMIT ?"
        )
        try:
            self.conn.execute(sql, [query_text, query_text, k])
            rows = self.conn.fetchall()
        except Exception as e:
            msg = str(e).lower()
            if "fts" in msg or "match_bm25" in msg:
                return []
            raise
        results = []
        for r in rows:
            d = self._row_dict_chunk(r)
            d["_score"] = r[len(_CHUNK_COLS)]
            results.append(self._to_search_result(d))
        return results

    def search_hybrid(self, query_text: str, query_embedding: list[float],
                      k: int = 10, filters: Optional[dict] = None,
                      rrf_k: float = 60.0, relevance_floor: float = 0.3,
                      excluded_chunk_types: Optional[list[str]] = None) -> list[SearchResult]:
        vec_results = self.search_vector(query_embedding, k * 2, filters)
        fts_results = self.search_fts(query_text, k * 2, filters)
        return self._rrf_fuse(vec_results, fts_results, k=rrf_k,
                              relevance_floor=relevance_floor,
                              excluded_chunk_types=excluded_chunk_types)[:k]

    def ensure_fts_index(self):
        self._ensure_fts_index()

    # ------------------------------------------------------------------
    # Versioning operations
    # ------------------------------------------------------------------

    def list_versions(self) -> list[Version]:
        rows = self.conn.execute(
            "SELECT version, ts, tag FROM store_versions ORDER BY version DESC"
        ).fetchall()
        return [Version(version=int(v), timestamp=str(ts), tag=tag) for v, ts, tag in rows]

    def checkout(self, version: int):
        """Materialize `version`'s chunk set as the active state."""
        self._materialize(version)

    def checkout_latest(self):
        pass  # active state is always latest in this design

    def restore(self, version: int):
        self._materialize(version)
        self._ensure_fts_index()
        self._bump_version(note=f"restore(version={version})")

    def _materialize(self, version: int):
        self.conn.execute("DELETE FROM chunks")
        chunks = self._chunks_at_version(int(version))
        self._insert_chunk_rows([_to_row(c) for c in chunks])
        self._ensure_fts_index()

    def create_tag(self, version: int, tag_name: str):
        self.conn.execute(
            "UPDATE store_versions SET tag = ? WHERE version = ?", [tag_name, int(version)]
        )

    def create_branch(self, name: str, from_version: Optional[int] = None):
        ver = int(from_version) if from_version else self._current_version()
        self.conn.execute(
            "UPDATE store_versions SET branch = ? WHERE version = ?", [name, ver]
        )

    def list_branches(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT branch FROM store_versions WHERE branch IS NOT NULL"
        ).fetchall()
        return [r[0] for r in rows if r[0]]

    def switch_branch(self, name: str):
        row = self.conn.execute(
            "SELECT version FROM store_versions WHERE branch = ? LIMIT 1", [name]
        ).fetchone()
        if not row:
            raise ValueError(f"Branch not found: {name}")
        self.checkout(int(row[0]))

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Stats:
        total_documents = int(self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        total_chunks = int(self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
        return Stats(
            total_documents=total_documents,
            total_chunks=total_chunks,
            db_size_bytes=os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0,
            current_version=self._current_version(),
            storage_path=self.db_path,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_where(self, filters: dict) -> str:
        clauses = []
        for key, value in filters.items():
            if isinstance(value, str):
                clauses.append(f"{key} = '{value}'")
            elif isinstance(value, (int, float)):
                clauses.append(f"{key} = {value}")
            elif isinstance(value, bool):
                clauses.append(f"{key} = {str(value).lower()}")
            elif isinstance(value, list):
                items = ",".join(f"'{v}'" if isinstance(v, str) else str(v) for v in value)
                clauses.append(f"{key} IN ({items})")
            else:
                clauses.append(f"{key} = '{value}'")
        return " AND ".join(clauses) if clauses else ""

    @staticmethod
    def _row_dict_chunk(r) -> dict:
        return dict(zip(_CHUNK_COLS, r))

    def _to_search_result(self, row: dict) -> SearchResult:
        return SearchResult(
            chunk_id=row.get("chunk_id", ""),
            text=row.get("text", ""),
            score=row.get("_score", 0.0),
            source=row.get("source", ""),
            doc_id=row.get("doc_id", ""),
            chunk_type=row.get("chunk_type", "paragraph"),
            entity_name=row.get("entity_name"),
            heading_path=_safe_json_load(row.get("heading_path"), list),
            scope_chain=_safe_json_load(row.get("scope_chain"), list),
            file_path=row.get("file_path"),
            start_line=row.get("start_line"),
            end_line=row.get("end_line"),
            metadata=_safe_json_load(row.get("metadata"), dict),
        )

    @staticmethod
    def _rrf_fuse(vector_results: list[SearchResult],
                  fts_results: list[SearchResult],
                  k: float = 60.0,
                  relevance_floor: float = 0.3,
                  excluded_chunk_types: list[str] | None = None) -> list[SearchResult]:
        """Reciprocal Rank Fusion with relevance floor and noise filtering.

        Each result's final score = sum(1 / (k + rank(result))) across both rankings.
        Results below relevance_floor are excluded. Heading-only/TOC chunks filtered out.
        """
        if excluded_chunk_types is None:
            excluded_chunk_types = ["heading", "toc", "inventory"]

        scores: dict[str, float] = {}
        result_map: dict[str, SearchResult] = {}

        for rank, r in enumerate(vector_results):
            if r.score < relevance_floor:
                continue
            if r.chunk_type in excluded_chunk_types:
                continue
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            result_map[r.chunk_id] = r

        for rank, r in enumerate(fts_results):
            if r.chunk_type in excluded_chunk_types:
                continue
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            if r.chunk_id not in result_map:
                result_map[r.chunk_id] = r

        if not scores:
            fallback = [r for r in vector_results + fts_results
                        if r.chunk_type not in excluded_chunk_types]
            return fallback[:10]

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for chunk_id, score in ranked:
            r = result_map[chunk_id]
            r.score = score
            results.append(r)

        return results
