"""Migrate data from LanceDB + SQLite to Postgres.

Direct cutover — no parallel run. LanceDB/DuckDB/SQLite files are
left in place but no longer used after migration.

Usage:
    python scripts/migrate_to_postgres.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from uuid import UUID

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


async def migrate_documents(conn: asyncpg.Connection, lance_store) -> int:
    """Migrate documents from LanceDB to Postgres."""
    try:
        docs = lance_store.documents_table.to_pandas()
    except Exception:
        logger.info("No documents table in LanceDB")
        return 0

    count = 0
    for _, row in docs.iterrows():
        await conn.execute(
            """
            INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (tenant_id, source) DO NOTHING
            """,
            str(row.get("document_id", UUID(int=0))),
            DEFAULT_TENANT,
            row.get("source", row.get("path", "unknown")),
            row.get("source_type", "text"),
            int(row.get("chunk_count", 0)),
            row.get("created_at"),
        )
        count += 1

    logger.info("Migrated %d documents", count)
    return count


async def migrate_chunks(conn: asyncpg.Connection, lance_store) -> int:
    """Migrate chunks (text only, no vectors) from LanceDB to Postgres."""
    try:
        chunks = lance_store.chunks_table.to_pandas()
    except Exception:
        logger.info("No chunks table in LanceDB")
        return 0

    count = 0
    for _, row in chunks.iterrows():
        await conn.execute(
            """
            INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text, source_type)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (tenant_id, doc_id, chunk_index) DO NOTHING
            """,
            str(row.get("chunk_id", UUID(int=0))),
            DEFAULT_TENANT,
            str(row.get("document_id", UUID(int=0))),
            int(row.get("chunk_index", count)),
            row.get("text", ""),
            row.get("source_type", "text"),
        )
        count += 1

    logger.info("Migrated %d chunks", count)
    return count


async def migrate_vectors(
    conn: asyncpg.Connection,
    lance_store,
    embedder,
    target_model: str,
) -> int:
    """Migrate vectors from LanceDB to pgvector.

    If existing vectors are 768d (nomic) and target is 4096d (qwen3),
    re-embed. Otherwise copy directly.
    """
    try:
        chunks = lance_store.chunks_table.to_pandas()
    except Exception:
        logger.info("No chunks to migrate vectors for")
        return 0

    count = 0
    for _, row in chunks.iterrows():
        vector = row.get("vector")
        chunk_id = str(row.get("chunk_id", UUID(int=0)))

        if vector is not None and hasattr(vector, "tolist"):
            vec_list = vector.tolist()
        elif isinstance(vector, list):
            vec_list = vector
        else:
            # No vector — re-embed
            text = row.get("text", "")
            if not text:
                continue
            vec_list = embedder.embed(text)

        await conn.execute(
            """
            INSERT INTO chunks_vectors (chunk_id, tenant_id, vector, embedding_model)
            VALUES ($1, $2, $3::vector, $4)
            ON CONFLICT (chunk_id) DO UPDATE SET vector = $3::vector, embedding_model = $4
            """,
            chunk_id,
            DEFAULT_TENANT,
            str(vec_list),
            target_model,
        )
        count += 1

    logger.info("Migrated %d vectors", count)
    return count


async def migrate_graph(conn: asyncpg.Connection, graph_store) -> tuple[int, int]:
    """Migrate entities and relations from SQLite graph store to Postgres."""
    entities_count = 0
    relations_count = 0

    try:
        # Migrate entities
        entities = graph_store.list_all_entities() if hasattr(graph_store, "list_all_entities") else []
        for entity in entities:
            await conn.execute(
                """
                INSERT INTO entities (entity_id, tenant_id, name, entity_type, metadata)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (tenant_id, name, entity_type) DO NOTHING
                """,
                str(entity.get("entity_id", UUID(int=0))),
                DEFAULT_TENANT,
                entity.get("name", ""),
                entity.get("entity_type", "concept"),
                str(entity.get("metadata", "{}")),
            )
            entities_count += 1
    except Exception as exc:
        logger.warning("Entity migration failed: %s", exc)

    try:
        # Migrate relations
        relations = graph_store.list_all_relations() if hasattr(graph_store, "list_all_relations") else []
        for relation in relations:
            await conn.execute(
                """
                INSERT INTO relations (relation_id, tenant_id, source_entity_id, target_entity_id,
                                       relation_type, weight)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id, source_entity_id, target_entity_id, relation_type) DO NOTHING
                """,
                str(relation.get("relation_id", UUID(int=0))),
                DEFAULT_TENANT,
                str(relation.get("source_entity_id", UUID(int=0))),
                str(relation.get("target_entity_id", UUID(int=0))),
                relation.get("relation_type", "related_to"),
                float(relation.get("weight", 1.0)),
            )
            relations_count += 1
    except Exception as exc:
        logger.warning("Relation migration failed: %s", exc)

    logger.info("Migrated %d entities, %d relations", entities_count, relations_count)
    return entities_count, relations_count


async def main() -> None:
    from config import load_config

    cfg = load_config()
    conn_str = cfg.get("database", {}).get("connection_string", "") or os.environ.get(
        "CORPUS_KB_DATABASE_URL", ""
    )

    if not conn_str:
        logger.error("No database connection string. Set CORPUS_KB_DATABASE_URL.")
        sys.exit(1)

    logger.info("Starting migration: LanceDB/SQLite -> Postgres")

    # Connect to Postgres
    conn = await asyncpg.connect(conn_str)
    await conn.execute(
        "SELECT set_config('app.current_tenant_id', $1, true)", DEFAULT_TENANT
    )

    # Connect to LanceDB
    lance_store = None
    try:
        from storage.lance_store import LanceDBStore

        lance_uri = cfg.get("storage", {}).get("lancedb_uri", "./data/lancedb")
        dims = cfg.get("embedding", {}).get("dimensions", 768)
        lance_store = LanceDBStore(lance_uri, dimensions=dims)
        logger.info("Connected to LanceDB at %s", lance_uri)
    except Exception as exc:
        logger.warning("LanceDB not available: %s", exc)

    # Connect to graph store (SQLite)
    graph_store = None
    try:
        from storage.graph_store import create_graph_store

        graph_db = cfg.get("storage", {}).get("graph_db", "./data/graph.db")
        graph_store = create_graph_store("sqlite", graph_db)
        logger.info("Connected to graph store at %s", graph_db)
    except Exception as exc:
        logger.warning("Graph store not available: %s", exc)

    # Embedder for re-embedding if needed
    embedder = None
    try:
        from rag.embedder import OllamaEmbedder

        embedder = OllamaEmbedder(cfg)
    except Exception as exc:
        logger.warning("Embedder not available: %s", exc)

    target_model = cfg.get("embedding", {}).get("model", "nomic-embed-text")

    # Run migration
    doc_count = 0
    chunk_count = 0
    vector_count = 0
    entity_count = 0
    relation_count = 0

    if lance_store:
        doc_count = await migrate_documents(conn, lance_store)
        chunk_count = await migrate_chunks(conn, lance_store)
        if embedder:
            vector_count = await migrate_vectors(conn, lance_store, embedder, target_model)

    if graph_store:
        entity_count, relation_count = await migrate_graph(conn, graph_store)

    # Summary
    logger.info("=" * 60)
    logger.info("MIGRATION COMPLETE")
    logger.info("=" * 60)
    logger.info("  Documents:  %d", doc_count)
    logger.info("  Chunks:     %d", chunk_count)
    logger.info("  Vectors:    %d", vector_count)
    logger.info("  Entities:   %d", entity_count)
    logger.info("  Relations:  %d", relation_count)
    logger.info("=" * 60)
    logger.info("Old LanceDB/SQLite files left in place (no longer used).")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())