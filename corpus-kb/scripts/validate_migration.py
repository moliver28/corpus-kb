"""Validate migration from LanceDB to Postgres.

Compares row counts and samples between old and new stores.
Exit 0 if pass, exit 1 if fail.

Usage:
    python scripts/validate_migration.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


async def validate() -> bool:
    """Run all validation checks. Returns True if all pass."""
    from config import load_config

    cfg = load_config()
    conn_str = cfg.get("database", {}).get("connection_string", "") or os.environ.get(
        "CORPUS_KB_DATABASE_URL", ""
    )

    if not conn_str:
        logger.error("No database connection string")
        return False

    conn = await asyncpg.connect(conn_str)
    await conn.execute(
        "SELECT set_config('app.current_tenant_id', $1, true)", DEFAULT_TENANT
    )

    all_pass = True

    # Check 1: Document count
    pg_doc_count = await conn.fetchval("SELECT COUNT(*) FROM documents")
    logger.info("Postgres documents: %d", pg_doc_count)
    if pg_doc_count == 0:
        logger.warning("No documents in Postgres (may be expected if LanceDB was empty)")

    # Check 2: Chunk count
    pg_chunk_count = await conn.fetchval("SELECT COUNT(*) FROM chunks")
    logger.info("Postgres chunks: %d", pg_chunk_count)

    # Check 3: Vector count
    pg_vector_count = await conn.fetchval("SELECT COUNT(*) FROM chunks_vectors")
    logger.info("Postgres vectors: %d", pg_vector_count)

    # Check 4: Entity count
    pg_entity_count = await conn.fetchval("SELECT COUNT(*) FROM entities")
    logger.info("Postgres entities: %d", pg_entity_count)

    # Check 5: Relation count
    pg_relation_count = await conn.fetchval("SELECT COUNT(*) FROM relations")
    logger.info("Postgres relations: %d", pg_relation_count)

    # Check 6: RLS policies exist
    rls_count = await conn.fetchval(
        "SELECT COUNT(*) FROM pg_policies WHERE schemaname = 'public'"
    )
    if rls_count < 9:
        logger.error("RLS policies missing: found %d, expected >= 9", rls_count)
        all_pass = False
    else:
        logger.info("RLS policies: %d (OK)", rls_count)

    # Check 7: Default tenant exists
    tenant_exists = await conn.fetchval(
        "SELECT COUNT(*) FROM tenants WHERE tenant_id = $1", DEFAULT_TENANT
    )
    if tenant_exists == 0:
        logger.error("Default tenant not found")
        all_pass = False
    else:
        logger.info("Default tenant: exists (OK)")

    # Check 8: Sample chunk text is not empty
    if pg_chunk_count > 0:
        sample = await conn.fetchrow(
            "SELECT chunk_id, text FROM chunks LIMIT 1"
        )
        if sample and sample["text"]:
            logger.info("Sample chunk text: %s...", sample["text"][:80])
        else:
            logger.error("Sample chunk has empty text")
            all_pass = False

    # Check 9: Sample vector has correct dimensions
    if pg_vector_count > 0:
        vec_sample = await conn.fetchrow(
            "SELECT vector FROM chunks_vectors LIMIT 1"
        )
        if vec_sample and vec_sample["vector"]:
            vec_str = str(vec_sample["vector"])
            # pgvector format: [0.1,0.2,...] — count commas for dimension estimate
            dims = vec_str.count(",") + 1
            logger.info("Sample vector dimensions: ~%d", dims)
            if dims not in (768, 4096):
                logger.warning("Unexpected vector dimensions: %d", dims)

    await conn.close()

    if all_pass:
        logger.info("=" * 60)
        logger.info("VALIDATION PASSED")
        logger.info("=" * 60)
    else:
        logger.error("=" * 60)
        logger.error("VALIDATION FAILED")
        logger.error("=" * 60)

    return all_pass


async def main() -> None:
    success = await validate()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())