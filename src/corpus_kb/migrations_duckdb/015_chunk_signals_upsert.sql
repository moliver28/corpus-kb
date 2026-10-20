-- ============================================================================
-- Migration 015: chunk_signals upsertable key (DuckDB mirror — pre-applied)
-- ============================================================================
-- Pair of migrations/015_chunk_signals_upsert.sql. The Postgres file drops
-- `detail` from the chunk_signals primary key
-- (chunk_id, code_id, signal, detail, tenant_id ->
-- chunk_id, code_id, signal, tenant_id), collapses stale duplicate rows,
-- and drops the append-only trigger. This mirror applies the end-state key
-- directly in 012, because DuckDB cannot alter a primary key and a fresh
-- install has no duplicate rows to collapse.
--
-- Also dropped here: the trigger drop. Postgres trigger
-- chunk_signals_append_only_trigger (body documented in 012) never existed
-- on DuckDB; the gateway blocks UPDATE/DELETE on the table instead.
-- No statement runs here; the file keeps the 1:1 numbering.
-- ============================================================================

SELECT 1 WHERE FALSE;
