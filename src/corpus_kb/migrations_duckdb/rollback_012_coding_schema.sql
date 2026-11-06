-- ============================================================================
-- Rollback migration 012 (DuckDB): drop the coding-domain tables.
-- ============================================================================
-- Pair of migrations/rollback_012_coding_schema.sql. Same reverse-dependency
-- drops; the pg trigger functions never existed here (the gateway's
-- APPEND_ONLY_TABLES guard owns the rule, so there is nothing to drop).
-- ============================================================================

DROP TABLE IF EXISTS coding_gate_decisions;
DROP TABLE IF EXISTS doc_extraction_audit;
DROP TABLE IF EXISTS chunk_status;
DROP TABLE IF EXISTS final_codes;
DROP TABLE IF EXISTS chunk_codes;
DROP TABLE IF EXISTS batch_status;
DROP TABLE IF EXISTS answer_ctx_vectors;
DROP TABLE IF EXISTS chunk_keyword_hits;
DROP TABLE IF EXISTS chunk_signals;
DROP TABLE IF EXISTS code_keywords;
DROP TABLE IF EXISTS codebook_versions;
DROP TABLE IF EXISTS code_registry;
