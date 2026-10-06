-- ============================================================================
-- Migration 012 Rollback: Drop all coding-domain tables in FK-safe order
-- ============================================================================

SET app.current_tenant_id = '00000000-0000-0000-0000-000000000001';

-- Drop in reverse dependency order (none depend on each other, so order is safe)
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

-- Drop trigger functions
DROP FUNCTION IF EXISTS chunk_signals_append_only();
DROP FUNCTION IF EXISTS chunk_codes_append_only();
DROP FUNCTION IF EXISTS coding_gate_decisions_append_only();
