-- ============================================================================
-- Migration 047: store meta table (width parity, issue #559 / G28)
-- ============================================================================
-- corpus.meta is a store-level key/value table. The migration runners write
-- the embedding_width row with the width they substituted into the DDL
-- (placeholder __EMBEDDING_DIMS__), and the boot guard compares that row
-- with embedding.dimensions at startup, so a width mismatch refuses at
-- boot instead of at the first ingest write (see _setup/store_width.py).
--
-- The default INSERT below covers stores migrated BEFORE this table
-- existed: every pre-parameterization store is 2560-wide by construction
-- (the DDL was hard-coded). The insert runs only when the key is absent,
-- so the runner's observed-width write for a store applied in this run
-- stays the last word.

CREATE TABLE IF NOT EXISTS corpus.meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT INTO corpus.meta (key, value)
    VALUES ('embedding_width', '2560')
    ON CONFLICT (key) DO NOTHING;
