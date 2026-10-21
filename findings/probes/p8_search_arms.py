"""W-D probe: validate the planned DuckDB search-arm SQL on the mirror schema.

Validates, on one gateway connection:
1. full migration mirror applies through gateway.execute;
2. FTS index created via PRAGMA through gateway.execute; watermark tables;
3. vector arm SQL (exact scan, zero_flag sentinel, $1::FLOAT[2560] cast);
4. FTS arm SQL (scalar macro shape + joins + tenant/dedup filters);
5. heading_path JSON column reads back as str (json.loads-able);
6. watermark drift detection after insert.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import importlib.resources as resources

from corpus_kb.storage.gateway import DuckDbGateway

TENANT = "11111111-1111-1111-1111-111111111111"
DOC = "22222222-2222-2222-2222-222222222222"
EMB = [0.1] * 2560
ZERO = [0.0] * 2560

VECTOR_ARM = """
WITH candidate AS MATERIALIZED (
    SELECT cv.chunk_id AS candidate_chunk_id,
           array_cosine_distance(cv.embedding, $1::FLOAT[2560]) AS dist,
           CASE WHEN sqrt(array_inner_product(cv.embedding, cv.embedding)) = 0
                THEN 1 ELSE 0 END AS zero_flag
    FROM chunks_vectors cv
    JOIN chunks ON cv.chunk_id = chunks.chunk_id
    JOIN documents ON chunks.doc_id = documents.doc_id
    WHERE cv.tenant_id = $2 {dedup} {degraded}
)
SELECT chunks.chunk_id, chunks.text, chunks.doc_id, documents.source,
       chunks.file_path, chunks.start_line, chunks.end_line, chunks.chunk_index,
       chunks.heading_path, (1 - candidate.dist) AS score
FROM candidate
JOIN chunks ON candidate.candidate_chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
ORDER BY candidate.zero_flag ASC, (1 - candidate.dist) + 0 DESC, chunks.chunk_id ASC
LIMIT $3
"""

FTS_ARM = """
SELECT chunks.chunk_id, chunks.text, chunks.doc_id, documents.source,
       chunks.file_path, chunks.start_line, chunks.end_line, chunks.chunk_index,
       chunks.heading_path, scored.score
FROM (
    SELECT chunks.chunk_id, fts_main_chunks.match_bm25(chunks.chunk_id, $1) AS score
    FROM chunks
) AS scored
JOIN chunks ON scored.chunk_id = chunks.chunk_id
JOIN documents ON chunks.doc_id = documents.doc_id
WHERE scored.score IS NOT NULL
  AND chunks.tenant_id = $2 AND chunks.tombstoned_at IS NULL AND chunks.superseded_at IS NULL
ORDER BY scored.score DESC, chunks.chunk_id ASC
LIMIT $3
"""


async def main() -> None:
    db = Path(__file__).parent / "p8.duckdb"
    if db.exists():
        db.unlink()
    from corpus_kb._setup.migrate import run_duckdb_migrations
    from corpus_kb.storage.gateway import duckdb_path_to_conn_string

    run_duckdb_migrations(duckdb_path_to_conn_string(db))
    gw = await DuckDbGateway.connect(str(db))
    try:
        print("1. migrations applied OK")

        await gw.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type, file_hash)"
            " VALUES (?, ?, ?, ?, ?)",
            DOC, TENANT, "probe.md", "text", "h1",
        )
        for i, (cid, text, emb, dead) in enumerate([
            ("c-aaa", "the quick brown fox jumps", EMB, False),
            ("c-bbb", "quick dog runs fast", EMB, False),
            ("c-zero", "quick zero vector row", ZERO, False),
            ("c-dead", "quick tombstoned row", EMB, True),
        ]):
            await gw.execute(
                "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text,"
                " heading_path) VALUES (?, ?, ?, ?, ?, ?)",
                cid, TENANT, DOC, i, text, '[\"a\",\"b\"]',
            )
            await gw.execute(
                "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding_model,"
                " embedding, degraded) VALUES (?, ?, ?, ?, ?)",
                cid, TENANT, "probe", emb, dead,
            )
        await gw.execute(
            "UPDATE chunks SET tombstoned_at = now() WHERE chunk_id = 'c-dead'"
        )
        print("2. seed rows OK")

        tag = await gw.execute(
            "PRAGMA create_fts_index('main.chunks', 'chunk_id', 'text')"
        )
        print(f"3. create_fts_index tag={tag!r}")
        n_docs = await gw.fetchval("SELECT count(*) FROM fts_main_chunks.docs")
        print(f"   index docs count = {n_docs}")

        q = [0.2] * 2560
        rows = await gw.fetch(VECTOR_ARM.format(
            dedup="AND chunks.tombstoned_at IS NULL AND chunks.superseded_at IS NULL",
            degraded="AND cv.degraded = FALSE",
        ), q, TENANT, 10)
        print(f"4. vector arm rows: {[(r['chunk_id'], round(r['score'], 4)) for r in rows]}")
        hp = rows[0]["heading_path"] if rows else None
        print(f"   heading_path type={type(hp).__name__} value={hp!r}")

        frows = await gw.fetch(FTS_ARM, "quick", TENANT, 10)
        print(f"5. fts arm rows: {[(r['chunk_id'], round(r['score'], 4)) for r in frows]}")

        # tricky queries: no crash
        for tricky in ['unclosed "quote', "知识库", "it's", "*", "(parens)"]:
            t = await gw.fetch(FTS_ARM, tricky, TENANT, 10)
            print(f"   tricky {tricky!r} -> {len(t)} rows (no error)")

        # drift: new chunk invisible until rebuild
        await gw.execute(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text)"
            " VALUES ('c-new', ?, ?, 4, 'fresh searchable needle')",
            TENANT, DOC,
        )
        await gw.execute(
            "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding_model, embedding, degraded)"
            " VALUES ('c-new', ?, 'probe', ?, FALSE)", TENANT, EMB,
        )
        n_chunks = await gw.fetchval("SELECT count(*) FROM chunks")
        stale = await gw.fetch(FTS_ARM, "needle", TENANT, 10)
        print(f"6. drift: chunks={n_chunks} index_docs={n_docs} needle_rows={len(stale)}")
        await gw.execute("PRAGMA drop_fts_index('main.chunks')")
        await gw.execute("PRAGMA create_fts_index('main.chunks', 'chunk_id', 'text')")
        fresh = await gw.fetch(FTS_ARM, "needle", TENANT, 10)
        print(f"   after rebuild needle_rows={len(fresh)}")

        # degraded probe SQL shape
        cnt = await gw.fetchval(
            "SELECT count(*)::float8 AS candidates FROM chunks_vectors cv"
            " JOIN chunks ON cv.chunk_id = chunks.chunk_id"
            " JOIN documents ON chunks.doc_id = documents.doc_id"
            " WHERE cv.tenant_id = $1 AND chunks.tombstoned_at IS NULL"
            " AND chunks.superseded_at IS NULL",
            TENANT,
        )
        print(f"7. degraded probe count={cnt}")
    finally:
        await gw.close()
        for suffix in ("", ".wal"):
            Path(f"{db}{suffix}").unlink(missing_ok=True)


asyncio.run(main())
