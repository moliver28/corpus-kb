#!/usr/bin/env python3
"""One-time migration: LanceDB store -> single DuckDB file.

Reads documents + chunks (with vectors) from an existing Lance directory
and writes them into the DuckDBStore schema. The lancedb package is only
needed to run this migration; the server itself no longer depends on it.

Usage:
    python scripts/migrate_lancedb_to_duckdb.py --lance ~/.corpus-kb/lancedb \
        --db ~/.corpus-kb/corpus.db --dimensions 768
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _normalize(value):
    """Legacy Lance writers stored list fields as flattened bracket strings
    ('[Corpus-KB, What you get]'), new ones store JSON. Accept both."""
    if not isinstance(value, str):
        return value
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(value)
            if not isinstance(parsed, str):
                return parsed
        except (ValueError, SyntaxError):
            continue
    if value == "None":
        return None
    s = value.strip()
    if len(s) > 1 and s[0] in ("[", "(") and s[-1] in ("]", ")"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [item.strip() for item in inner.split(",") if item.strip()]
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lance", required=True, help="Existing LanceDB directory")
    ap.add_argument("--db", required=True, help="Target corpus.db path")
    ap.add_argument("--dimensions", type=int, default=768)
    args = ap.parse_args()

    import lancedb  # only needed for the migration

    from storage.duckdb_store import DuckDBStore
    from utils.models import Chunk, Document

    lance = lancedb.connect(str(Path(args.lance).expanduser().resolve()))
    store = DuckDBStore(args.db, dimensions=args.dimensions)

    docs = lance.open_table("documents").search().limit(100000).to_list()
    n_docs = 0
    # Documents
    for doc in docs:
        store.insert_document(Document(
            doc_id=doc["doc_id"],
            source=doc.get("source", ""),
            source_type=doc.get("source_type", "text"),
            metadata=_normalize(doc.get("metadata")) or {},
            chunk_count=int(doc.get("chunk_count", 0)),
            created_at=str(doc.get("created_at", "")),
        ))
        n_docs += 1

    chunks = lance.open_table("chunks").search().limit(1000000).to_list()
    batch: list[Chunk] = []
    n_chunks = 0
    for c in chunks:
        c = dict(c)
        for field in ("metadata", "heading_path", "scope_chain"):
            c[field] = _normalize(c.get(field))
        batch.append(Chunk.from_row(c))
        if len(batch) >= 500:
            store.insert_chunks(batch)
            n_chunks += len(batch)
            batch.clear()
    if batch:
        store.insert_chunks(batch)
        n_chunks += len(batch)

    stats = store.get_stats()
    print(json.dumps({
        "migrated_documents": n_docs,
        "migrated_chunks": n_chunks,
        "stats": {
            "total_documents": stats.total_documents,
            "total_chunks": stats.total_chunks,
            "current_version": stats.current_version,
        },
        "target": store.db_path,
    }, indent=2))
    return 0


if __name__ == "__main__":
    import json
    raise SystemExit(main())
