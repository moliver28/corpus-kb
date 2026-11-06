-- ============================================================================
-- Migration 043: the project_tables registry (Track C, DuckDB mirror)
-- ============================================================================
-- Pair of migrations/043_project_tables_registry.sql. Static catalogue of
-- the runtime raw_<slug> tables that storage/project_tables.py creates.
-- VARCHAR holds the uuid text on DuckDB; no RLS (the WHERE tenant_id
-- predicates stay the filter there). The raw tables themselves are
-- runtime DDL and are NOT migration files.
-- ============================================================================

CREATE TABLE IF NOT EXISTS project_tables (
    id VARCHAR DEFAULT gen_random_uuid() PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    project_id VARCHAR,
    table_name VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, table_name)
);
