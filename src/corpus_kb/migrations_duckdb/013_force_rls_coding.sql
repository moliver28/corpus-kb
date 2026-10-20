-- ============================================================================
-- Migration 013: FORCE row-level security, coding domain (DuckDB — dropped)
-- ============================================================================
-- Pair of migrations/013_force_rls_coding.sql. It marks the twelve coding
-- tables FORCE ROW LEVEL SECURITY. DuckDB has no row-level security; the
-- gateway carries the explicit tenant_id predicate on every statement
-- (program doc sections 1.1 and 3.1). Tables touched on Postgres:
-- code_registry, codebook_versions, code_keywords, chunk_signals,
-- chunk_keyword_hits, answer_ctx_vectors, batch_status, chunk_codes,
-- final_codes, chunk_status, doc_extraction_audit, coding_gate_decisions.
-- No statement runs here; the file keeps the 1:1 numbering.
-- ============================================================================

SELECT 1 WHERE FALSE;
