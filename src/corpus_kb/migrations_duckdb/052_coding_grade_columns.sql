-- ============================================================================
-- Migration 052: grade + grade_reason on the decision tables (TAO T1, TD-07)
-- — DuckDB mirror
-- ============================================================================
-- Pair of migrations/051_coding_grade_columns.sql. Translation rules (duckdb
-- 000 header): uuid -> VARCHAR; timestamptz stays. RLS policies and the DO
-- policy block are dropped; the gateway enforces tenant scoping. The CHECK
-- closed list on grade_reason does NOT translate: DuckDB cannot add a
-- column with an inline constraint ("Adding columns with constraints not
-- yet supported"), so the duckdb engine enforces the closed list at the
-- application layer — coding/grade.py's GRADE_REASONS is the only writer.
-- Additive only: no rollback mirror.

ALTER TABLE chunk_codes ADD COLUMN IF NOT EXISTS grade TEXT;
ALTER TABLE chunk_codes ADD COLUMN IF NOT EXISTS grade_reason TEXT;

ALTER TABLE final_codes ADD COLUMN IF NOT EXISTS grade TEXT;
ALTER TABLE final_codes ADD COLUMN IF NOT EXISTS grade_reason TEXT;

ALTER TABLE research_assignments ADD COLUMN IF NOT EXISTS grade TEXT;
ALTER TABLE research_assignments ADD COLUMN IF NOT EXISTS grade_reason TEXT;
