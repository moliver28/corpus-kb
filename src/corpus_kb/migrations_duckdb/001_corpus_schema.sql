-- ============================================================================
-- Migration 001: corpus legacy sources/nodes/index_generations (DuckDB mirror)
-- ============================================================================
-- Pair of migrations/001_corpus_schema.sql. UUID -> VARCHAR, JSONB -> JSON,
-- and the SERIAL key becomes a sequence default (DuckDB has no SERIAL).
--
-- The three tables land in main, not in the corpus schema: DuckDB names a
-- database after its file stem, so on a file named corpus.duckdb every
-- corpus.<table> reference is ambiguous between that catalog and the schema
-- and fails to bind. The corpus schema itself is still created (pair
-- parity); it just stays empty here.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS corpus;

CREATE SEQUENCE IF NOT EXISTS sources_id_seq START 1;

CREATE TABLE IF NOT EXISTS sources (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_path TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT,
    git_rev TEXT,
    content_hash TEXT NOT NULL,
    ingested_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (repo_path, file_path, git_rev)
);

CREATE TABLE IF NOT EXISTS nodes (
    id VARCHAR PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id VARCHAR REFERENCES sources(id),
    -- Postgres action: ON DELETE CASCADE; the gateway enforces it here.
    chunk_index INT NOT NULL,
    text TEXT NOT NULL,
    metadata JSON DEFAULT '{}'
);

CREATE SEQUENCE IF NOT EXISTS index_generations_id_seq START 1;

CREATE TABLE IF NOT EXISTS index_generations (
    id INTEGER PRIMARY KEY DEFAULT nextval('index_generations_id_seq'),
    embed_model TEXT NOT NULL,
    embed_dim INT NOT NULL,
    table_name TEXT NOT NULL UNIQUE,
    active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);
