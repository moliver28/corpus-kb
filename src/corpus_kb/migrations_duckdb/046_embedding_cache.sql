-- ============================================================================
-- Migration 046 (DuckDB): persistent embedding cache (issue #539). Twin of
-- migrations/046_embedding_cache_2560.sql. Translation per the house convention
-- (see 034 header): halfvec(2560) -> FLOAT[2560]. DuckDB has no RLS
-- (single-writer file engine).
--
-- One row per (sha256, model); lookup always filters on model, so a model
-- flip never serves a stale vector. The sha256 key covers the exact text,
-- including the query-side instruction prefix.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS.
-- ============================================================================

CREATE TABLE IF NOT EXISTS embedding_cache_2560 (
    sha256 VARCHAR(64) PRIMARY KEY,
    model TEXT NOT NULL,
    vector FLOAT[2560] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
