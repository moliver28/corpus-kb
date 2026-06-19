"""LanceDB storage backend — primary store for chunks, embeddings, and documents.

Features:
- Vector search with SQL filters
- Full-text search via LanceDB FTS
- Hybrid search (vector + FTS + Reciprocal Rank Fusion)
- Automatic versioning with time-travel, branches, and tags
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import lancedb
import pyarrow as pa

from utils.models import Chunk, Document, SearchResult, Version, Branch, Stats


def _resolve_uri(uri: str) -> str:
    """Resolve ~/ to user home directory."""
    return str(Path(uri).expanduser().resolve())


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


class LanceDBStore:
    """Manages all LanceDB table operations."""

    def __init__(self, uri: str, dimensions: int = 4096):
        self.uri = _resolve_uri(uri)
        self.dimensions = dimensions
        os.makedirs(self.uri, exist_ok=True)
        self.db = lancedb.connect(self.uri)
        self._ensure_tables()

    # ------------------------------------------------------------------
    # Table schema management
    # ------------------------------------------------------------------

    def _ensure_tables(self):
        """Create default tables if they don't exist."""
        existing = self.db.list_tables() if hasattr(self.db, "list_tables") else self.db.table_names()
        for tbl in ["documents", "chunks"]:
            if tbl not in existing:
                if tbl == "documents":
                    schema = pa.schema([
                        pa.field("doc_id", pa.utf8()),
                        pa.field("source", pa.utf8()),
                        pa.field("source_type", pa.utf8()),
                        pa.field("metadata", pa.utf8()),     # JSON string
                        pa.field("chunk_count", pa.int32()),
                        pa.field("created_at", pa.utf8()),
                    ])
                    self.db.create_table(tbl, schema=schema, mode="overwrite")
                elif tbl == "chunks":
                    schema = pa.schema([
                        pa.field("chunk_id", pa.utf8()),
                        pa.field("doc_id", pa.utf8()),
                        pa.field("text", pa.utf8()),
                        pa.field("vector", pa.list_(pa.float32(), self.dimensions)),
                        pa.field("chunk_index", pa.int32()),
                        pa.field("source", pa.utf8()),
                        pa.field("source_type", pa.utf8()),
                        pa.field("metadata", pa.utf8()),
                        pa.field("heading_path", pa.utf8()),
                        pa.field("parent_chunk_id", pa.utf8()),
                        pa.field("sibling_order", pa.int32()),
                        pa.field("sibling_count", pa.int32()),
                        pa.field("scope_chain", pa.utf8()),
                        pa.field("chunk_type", pa.utf8()),
                        pa.field("entity_name", pa.utf8()),
                        pa.field("file_path", pa.utf8()),
                        pa.field("start_line", pa.int32()),
                        pa.field("end_line", pa.int32()),
                        pa.field("created_at", pa.utf8()),
                    ])
                    tbl_created = self.db.create_table(tbl, schema=schema, mode="overwrite")
                    # Create FTS index on text column for full-text search
                    tbl_created.create_fts_index("text", replace=True)

    @property
    def chunks_table(self):
        return self.db.open_table("chunks")

    @property
    def documents_table(self):
        return self.db.open_table("documents")

    # ------------------------------------------------------------------
    # Document operations
    # ------------------------------------------------------------------

    def insert_document(self, doc: Document) -> str:
        """Insert a document record. Returns doc_id."""
        self.documents_table.add([doc.to_lance()])
        return doc.doc_id

    def list_documents(self) -> list[dict]:
        """List all documents."""
        return self.documents_table.search().limit(1000).to_list()

    def get_document(self, doc_id: str) -> Optional[dict]:
        """Get document by ID."""
        results = self.documents_table.search().where(f"doc_id = '{doc_id}'").to_list()
        return results[0] if results else None

    def delete_document(self, doc_id: str) -> bool:
        """Delete a document and all its chunks. Returns True if found."""
        doc = self.get_document(doc_id)
        if not doc:
            return False
        self.documents_table.delete(f"doc_id = '{doc_id}'")
        self.chunks_table.delete(f"doc_id = '{doc_id}'")
        return True

    # ------------------------------------------------------------------
    # Chunk operations
    # ------------------------------------------------------------------

    def insert_chunks(self, chunks: list[Chunk]) -> int:
        """Batch insert chunks. LanceDB automatically versions the write. Returns count."""
        if not chunks:
            return 0
        data = [c.to_lance() for c in chunks]
        self.chunks_table.add(data)
        return len(chunks)

    def get_chunk(self, chunk_id: str) -> Optional[dict]:
        """Get a single chunk by ID."""
        results = self.chunks_table.search().where(f"chunk_id = '{chunk_id}'").limit(1).to_list()
        return results[0] if results else None

    def get_chunk_context(self, chunk_id: str, before: int = 2,
                          after: int = 2) -> list[SearchResult]:
        """Get surrounding chunks for context expansion."""
        chunk = self.get_chunk(chunk_id)
        if not chunk:
            return []

        doc_id = chunk.get("doc_id", "")
        chunk_idx = chunk.get("chunk_index", 0)
        start_idx = max(0, chunk_idx - before)
        end_idx = chunk_idx + after

        results = self.chunks_table.search().where(
            f"doc_id = '{doc_id}' AND chunk_index >= {start_idx} AND chunk_index <= {end_idx}"
        ).limit(100).to_list()

        return [self._to_search_result(r) for r in results]

    # ------------------------------------------------------------------
    # Search operations
    # ------------------------------------------------------------------

    def search_vector(self, query_embedding: list[float], k: int = 10,
                      filters: Optional[dict] = None) -> list[SearchResult]:
        """Vector search with optional SQL-style filters."""
        tbl = self.chunks_table
        if tbl.count_rows() == 0:
            return []
        query = tbl.search(query_embedding)

        if filters:
            where_clause = self._build_where(filters)
            if where_clause:
                query = query.where(where_clause)

        results = query.limit(k).to_list()
        return [self._to_search_result(r) for r in results]

    def search_fts(self, query_text: str, k: int = 10,
                   filters: Optional[dict] = None) -> list[SearchResult]:
        """Full-text search using LanceDB FTS index."""
        tbl = self.chunks_table
        try:
            query = tbl.search(query_text, query_type="fts")

            if filters:
                where_clause = self._build_where(filters)
                if where_clause:
                    query = query.where(where_clause)

            results = query.limit(k).to_list()
            return [self._to_search_result(r) for r in results]
        except Exception as e:
            # FTS index may not exist yet; return empty gracefully
            if "fts" in str(e).lower():
                return []
            raise

    def search_hybrid(self, query_text: str, query_embedding: list[float],
                      k: int = 10, filters: Optional[dict] = None,
                      rrf_k: float = 60.0, relevance_floor: float = 0.3,
                      excluded_chunk_types: Optional[list[str]] = None) -> list[SearchResult]:
        """Hybrid search: vector + FTS fused via Reciprocal Rank Fusion.

        Args:
            query_text: Text for FTS search.
            query_embedding: Embedding for vector search.
            k: Number of results to return.
            filters: Optional SQL filters (dict of column -> value).
            rrf_k: RRF constant (default 60).
            relevance_floor: Minimum vector score to include (default 0.3).
            excluded_chunk_types: Chunk types to exclude (default ["heading", "toc", "inventory"]).

        Returns:
            Ranked list of SearchResult with fused scores.
        """
        vec_results = self.search_vector(query_embedding, k * 2, filters)
        fts_results = self.search_fts(query_text, k * 2, filters)

        return self._rrf_fuse(vec_results, fts_results, k=rrf_k,
                             relevance_floor=relevance_floor,
                             excluded_chunk_types=excluded_chunk_types)[:k]

    def ensure_fts_index(self):
        """Ensure FTS index exists on the chunks table."""
        tbl = self.chunks_table
        try:
            tbl.search("test", query_type="fts")
        except Exception:
            # Create FTS index
            tbl.create_fts_index("text", replace=True)

    # ------------------------------------------------------------------
    # Versioning operations
    # ------------------------------------------------------------------

    def list_versions(self) -> list[Version]:
        """List all versions of the chunks table."""
        tbl = self.chunks_table
        tags_map = tbl.tags.list() if hasattr(tbl, "tags") else {}
        versions = []
        for v in tbl.list_versions():
            ver = v.get("version", 0)
            ts = v.get("timestamp", "")
            if isinstance(ts, datetime):
                ts = ts.isoformat()
            tag = tags_map.get(str(ver), {}).get("name") if isinstance(tags_map, dict) else None
            if not tag and isinstance(tags_map, dict):
                for tname, tinfo in tags_map.items():
                    if isinstance(tinfo, dict) and tinfo.get("version") == ver:
                        tag = tname
                        break
            versions.append(Version(version=ver, timestamp=str(ts), tag=tag))
        return sorted(versions, key=lambda x: x.version, reverse=True)

    def checkout(self, version: int):
        """Time-travel to a specific version (read-only)."""
        self.chunks_table.checkout(version)

    def checkout_latest(self):
        """Return to latest version."""
        self.chunks_table.checkout_latest()

    def restore(self, version: int):
        """Rollback table to a specific version (creates new commit)."""
        self.chunks_table.restore(version)

    def create_tag(self, version: int, tag_name: str):
        """Create an immutable tag pointing to a version."""
        tbl = self.chunks_table
        tbl.tags.create(tag_name, version)

    def create_branch(self, name: str, from_version: Optional[int] = None):
        """Create a new branch. Note: LanceDB branch API may vary by version."""
        tbl = self.chunks_table
        ver = from_version or tbl.version
        tbl.tags.create(f"branch:{name}", ver)

    def list_branches(self) -> list[str]:
        """List all branch names."""
        tbl = self.chunks_table
        return [
            t["name"].replace("branch:", "", 1)
            for t in tbl.tags.list()
            if t["name"].startswith("branch:")
        ]

    def switch_branch(self, name: str):
        """Switch to a branch (checkout its tagged version)."""
        tbl = self.chunks_table
        for t in tbl.tags.list():
            if t["name"] == f"branch:{name}":
                tbl.checkout(t["version"])
                return
        raise ValueError(f"Branch not found: {name}")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Stats:
        """Get database statistics."""
        docs = self.documents_table.search().limit(1).to_list()
        chunks = self.chunks_table.search().limit(1).to_list()
        total_docs = len(self.documents_table.search().limit(10000).to_list())
        total_chunks = len(self.chunks_table.search().limit(10000).to_list())

        # Estimate DB size
        db_size = 0
        if os.path.exists(self.uri):
            for root, dirs, files in os.walk(self.uri):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        db_size += os.path.getsize(fp)
                    except OSError:
                        pass

        return Stats(
            total_documents=total_docs,
            total_chunks=total_chunks,
            db_size_bytes=db_size,
            current_version=self.chunks_table.version,
            storage_path=self.uri,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_where(self, filters: dict) -> str:
        """Build SQL WHERE clause from a dict of {column: value}."""
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

    def _to_search_result(self, row: dict) -> SearchResult:
        """Convert a LanceDB row to a SearchResult."""
        return SearchResult(
            chunk_id=row.get("chunk_id", ""),
            text=row.get("text", ""),
            score=row.get("_distance", 0.0),
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
                  excluded_chunk_types: Optional[list[str]] = None) -> list[SearchResult]:
        """Reciprocal Rank Fusion with relevance floor and chunk type filtering.

        Each result's final score = sum(1 / (k + rank(result))) across both rankings.
        
        Args:
            vector_results: Results from vector search.
            fts_results: Results from full-text search.
            k: RRF constant (default 60.0).
            relevance_floor: Minimum vector score to include (default 0.3 for nomic-embed-text).
            excluded_chunk_types: Chunk types to exclude (default ["heading", "toc", "inventory"]).
        
        Returns:
            Ranked list of SearchResult, filtered by relevance and chunk type.
            If all chunks filtered, returns top-k by relevance from original results.
        """
        if excluded_chunk_types is None:
            excluded_chunk_types = ["heading", "toc", "inventory"]
        
        scores: dict[str, float] = {}
        result_map: dict[str, SearchResult] = {}

        for rank, r in enumerate(vector_results):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            result_map[r.chunk_id] = r

        for rank, r in enumerate(fts_results):
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + 1.0 / (k + rank + 1)
            if r.chunk_id not in result_map:
                result_map[r.chunk_id] = r

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for chunk_id, score in ranked:
            r = result_map[chunk_id]
            
            # Filter by chunk type
            if r.chunk_type in excluded_chunk_types:
                continue
            
            # Filter by relevance floor (use vector score as proxy for relevance)
            if r.score < relevance_floor:
                continue
            
            r.score = score
            results.append(r)

        # Edge case: if all chunks filtered, return top-k by original relevance
        if not results:
            all_results = list(result_map.values())
            all_results.sort(key=lambda x: x.score, reverse=True)
            return all_results[:int(k)]

        return results
