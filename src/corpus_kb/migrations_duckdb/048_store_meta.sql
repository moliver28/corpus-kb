-- ============================================================================
-- Migration 048 (DuckDB): store meta table (width parity, issue #559 / G28)
-- ============================================================================
-- Pair of migrations/047_store_meta.sql. The table lands in main, not in
-- the corpus schema: DuckDB names a database after its file stem, so on a
-- file named corpus.duckdb every corpus.<table> reference is ambiguous
-- between that catalog and the schema (the migration-001 rule).
--
-- The migration runners write the embedding_width row with the width they
-- substituted into the DDL (placeholder __EMBEDDING_DIMS__), and the boot
-- guard compares that row with embedding.dimensions at startup, so a
-- width mismatch refuses at boot instead of at the first ingest write
-- (see _setup/store_width.py).
--
-- The default INSERT below covers stores migrated BEFORE this table
-- existed: every pre-parameterization store is 2560-wide by construction
-- (the DDL was hard-coded). The insert runs only when the key is absent,
-- so the runner's observed-width write for a store applied in this run
-- stays the last word.

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO meta (key, value)
    VALUES ('embedding_width', '2560')
    ON CONFLICT (key) DO NOTHING;
