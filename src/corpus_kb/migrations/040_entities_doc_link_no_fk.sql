-- ============================================================================
-- Migration 040: drop the entities -> documents foreign key (issue #330)
-- ============================================================================
-- DuckDB's in-place update regime (issue #330, verified on 1.5.6) turns an
-- UPDATE of a foreign-key column into a delete plus an insert, and the
-- insert fails while relations still reference the entities row. The delete
-- cascade's SET NULL stage updates entities.source_document_id, so the FK
-- must go; the gateway-enforced SET NULL action lives in
-- storage.duckdb_cascade now. Postgres drops the same FK so the two engines
-- keep one declared shape (the parity gate declares the fold difference —
-- sqlglot cannot see a DROP CONSTRAINT, and DuckDB has no such ALTER).
-- ============================================================================

ALTER TABLE entities
    DROP CONSTRAINT IF EXISTS entities_source_document_id_fkey;
