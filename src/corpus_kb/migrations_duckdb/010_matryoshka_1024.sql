-- ============================================================================
-- Migration 010: matryoshka two-tier slice column (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/010_matryoshka_1024.sql. Adds chunks_vectors.vector_1024
-- (FLOAT[1024] here, vector(1024) on Postgres). The Postgres backfill
-- (l2_normalize(subvector(...)) over legacy rows) is dropped: a fresh DuckDB
-- install runs this on an empty table, and the write path maintains the
-- slice per row afterwards. The HNSW index is dropped too; DuckDB defaults
-- to exact scan (opt-in vss is runtime-managed).
-- ============================================================================

ALTER TABLE chunks_vectors ADD COLUMN IF NOT EXISTS vector_1024 FLOAT[1024];
