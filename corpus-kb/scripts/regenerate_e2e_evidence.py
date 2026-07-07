"""Regenerate E2E evidence JSON for Task 8.

Runs the full ingest pipeline on tests/fixtures/ontology_sample.md and
captures: document_id, chunk_ids with char offsets + text_preview,
entity_ids with extractor_id/entity_type/chunk_id/char offsets,
relation_ids with relation_type/extractor_id, and lance_row_count.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.storage.graph_store import SQLiteGraphStore
from src.tools.ingest_tools import ingest_file


def main() -> None:
    fixture_dir = Path("tests/fixtures/langextract_recorded")
    sample_md = Path("tests/fixtures/ontology_sample.md")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config = load_config()
        storage = cast(dict[str, object], config.setdefault("storage", {}))
        storage["lancedb_uri"] = str(tmp_path / "lancedb")
        storage["graph_db"] = str(tmp_path / "graph.db")
        graph = cast(dict[str, object], config.setdefault("graph", {}))
        graph["extractor"] = "langextract"
        graph["fixture_dir"] = str(fixture_dir.resolve())
        graph["live_fallback"] = False
        embedding = cast(dict[str, object], config.setdefault("embedding", {}))
        embedding.setdefault("model", "nomic-embed-text")
        embedding.setdefault("dimensions", 768)
        embedding.setdefault("base_url", "http://localhost:11434")
        embedding.setdefault("batch_size", 32)

        store = SQLiteGraphStore(tmp_path / "graph.db")
        result = ingest_file(str(sample_md), graph_store=store, config=config)

        # Collect chunk, entity, and relation details
        import sqlite3

        chunks_data: list[dict[str, object]] = []
        entities_data: list[dict[str, object]] = []
        relations_data: list[dict[str, object]] = []

        conn = store._open_connection()
        try:
            conn.row_factory = sqlite3.Row
            for row in conn.execute(
                "SELECT chunk_id, source_start_char, source_end_char, text "
                "FROM chunks WHERE document_id = ? ORDER BY source_start_char",
                (result["document_id"],),
            ).fetchall():
                text_preview = (row["text"] or "")[:80]
                chunks_data.append({
                    "chunk_id": row["chunk_id"],
                    "source_start_char": row["source_start_char"],
                    "source_end_char": row["source_end_char"],
                    "text_preview": text_preview,
                })

            for row in conn.execute(
                "SELECT entity_id, name, entity_type, extractor_id, chunk_id, "
                "source_start_char, source_end_char "
                "FROM entities WHERE source_document_id = ?",
                (result["document_id"],),
            ).fetchall():
                entities_data.append({
                    "entity_id": row["entity_id"],
                    "name": row["name"],
                    "entity_type": row["entity_type"],
                    "extractor_id": row["extractor_id"],
                    "chunk_id": row["chunk_id"],
                    "source_start_char": row["source_start_char"],
                    "source_end_char": row["source_end_char"],
                })

            for row in conn.execute(
                "SELECT relation_id, relation_type, extractor_id "
                "FROM relations WHERE chunk_id IN "
                "(SELECT chunk_id FROM chunks WHERE document_id = ?)",
                (result["document_id"],),
            ).fetchall():
                relations_data.append({
                    "relation_id": row["relation_id"],
                    "relation_type": row["relation_type"],
                    "extractor_id": row["extractor_id"],
                })
        finally:
            conn.close()

        store.close()

        evidence = {
            "status": result["status"],
            "document_id": result["document_id"],
            "path": result["path"],
            "source_type": result["source_type"],
            "size_bytes": result["size_bytes"],
            "chunk_count": result["chunk_count"],
            "entity_count": result["entity_count"],
            "relation_count": result["relation_count"],
            "lance_row_count": result["lance_row_count"],
            "degraded": result["degraded"],
            "extractor_id": result["extractor_id"],
            "errors": result["errors"],
            "chunks": chunks_data,
            "entities": entities_data,
            "relations": relations_data,
        }

    out_path = Path(".omo/evidence/task-8-pr-review-fixes.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"Evidence written to {out_path}")
    print(f"  chunks: {len(chunks_data)}")
    print(f"  entities: {len(entities_data)}")
    print(f"  relations: {len(relations_data)}")
    print(f"  lance_row_count: {evidence['lance_row_count']}")
    print(f"  degraded: {evidence['degraded']}")
    print(f"  errors: {evidence['errors']}")


if __name__ == "__main__":
    main()