"""Regenerate E2E evidence JSON for Task 8.

Runs the full ingest pipeline on tests/fixtures/ontology_sample.md and
captures: document_id, chunk_ids with char offsets + text_preview,
entity_ids with extractor_id/entity_type/chunk_id/char offsets,
relation_ids with relation_type/extractor_id, and pg_chunk_count.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import cast

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config
from src.tools.ingest_tools import ingest_file


async def main() -> None:
    fixture_dir = Path("tests/fixtures/langextract_recorded")
    sample_md = Path("tests/fixtures/ontology_sample.md")

    config = load_config()
    graph = cast(dict[str, object], config.setdefault("graph", {}))
    graph["extractor"] = "langextract"
    graph["fixture_dir"] = str(fixture_dir.resolve())
    graph["live_fallback"] = False
    embedding = cast(dict[str, object], config.setdefault("embedding", {}))
    embedding.setdefault("model", "nomic-embed-text")
    embedding.setdefault("dimensions", 768)
    embedding.setdefault("base_url", "http://localhost:11434")
    embedding.setdefault("batch_size", 32)

    db_cfg = cast(dict[str, object], config.setdefault("database", {}))
    conn_str = db_cfg.get("connection_string", "postgresql://corpus_user:corpus_pass@localhost:5432/corpus_kb")

    pool = await asyncpg.create_pool(conn_str)
    try:
        result = await ingest_file(str(sample_md), pool, config=config)

        # Collect chunk, entity, and relation details from Postgres
        chunks_data: list[dict[str, object]] = []
        entities_data: list[dict[str, object]] = []
        relations_data: list[dict[str, object]] = []

        async with pool.acquire() as conn:
            await conn.execute(
                "SELECT set_config('app.current_tenant_id', $1, true)",
                "00000000-0000-0000-0000-000000000001",
            )
            rows = await conn.fetch(
                "SELECT chunk_id::text, text FROM chunks WHERE doc_id = $1 ORDER BY chunk_index",
                result["document_id"],
            )
            for row in rows:
                chunks_data.append({
                    "chunk_id": row["chunk_id"],
                    "text_preview": (row["text"] or "")[:80],
                })

            rows = await conn.fetch(
                "SELECT entity_id::text, name, entity_type, metadata::text FROM entities WHERE source_document_id = $1",
                result["document_id"],
            )
            for row in rows:
                entities_data.append({
                    "entity_id": row["entity_id"],
                    "name": row["name"],
                    "entity_type": row["entity_type"],
                })

        evidence = {
            "status": result["status"],
            "document_id": result["document_id"],
            "path": result["path"],
            "source_type": result["source_type"],
            "size_bytes": result["size_bytes"],
            "chunk_count": result["chunk_count"],
            "entity_count": result["entity_count"],
            "relation_count": result["relation_count"],
            "pg_chunk_count": result.get("pg_chunk_count", 0),
            "pg_vector_count": result.get("pg_vector_count", 0),
            "degraded": result["degraded"],
            "extractor_id": result["extractor_id"],
            "errors": result["errors"],
            "chunks": chunks_data,
            "entities": entities_data,
            "relations": relations_data,
        }
    finally:
        await pool.close()

    out_path = Path(".omo/evidence/task-8-pr-review-fixes.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"Evidence written to {out_path}")
    print(f"  chunks: {len(chunks_data)}")
    print(f"  entities: {len(entities_data)}")
    print(f"  relations: {len(relations_data)}")
    print(f"  pg_chunk_count: {evidence['pg_chunk_count']}")
    print(f"  degraded: {evidence['degraded']}")
    print(f"  errors: {evidence['errors']}")


if __name__ == "__main__":
    asyncio.run(main())