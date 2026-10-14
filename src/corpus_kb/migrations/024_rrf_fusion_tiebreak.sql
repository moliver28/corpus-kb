-- ============================================================================
-- Migration 024: deterministic tie order for corpus.rrf_fusion (zero-drift
-- program, Wave 2 package C / rule 9)
-- ============================================================================
-- corpus.rrf_fusion (migration 006) ordered fused ties by first appearance
-- in the input arrays. That inherits the arm output order, which before the
-- query-determinism change was score-only ORDER BY — unstable across runs
-- on tied scores. This migration replaces the tiebreak with chunk id ASC:
-- the fusion result is now a total order (fused score DESC, then chunk_id
-- ASC), so a harness can snapshot and cache fused results.
--
-- Idempotent: CREATE OR REPLACE FUNCTION, same signature and return shape
-- as migration 006. The first_seen CTE from 006 is gone — its only use was
-- the old tie order. Rollback: rollback_024_rrf_fusion_tiebreak.sql
-- re-applies the 006 body verbatim.
-- ============================================================================

CREATE OR REPLACE FUNCTION corpus.rrf_fusion(
    vector_results JSONB,
    fts_results JSONB,
    k INT,
    rrf_k INT DEFAULT 60
)
RETURNS TABLE (
    chunk_id UUID,
    text TEXT,
    source TEXT,
    doc_id UUID,
    score FLOAT8
)
LANGUAGE sql
IMMUTABLE
AS $$
    WITH vec AS (
        SELECT (elem ->> 'chunk_id')::uuid AS cid,
               elem ->> 'text' AS txt,
               elem ->> 'source' AS src,
               (elem ->> 'doc_id')::uuid AS did,
               ord
        FROM jsonb_array_elements(vector_results) WITH ORDINALITY AS v(elem, ord)
    ),
    fts AS (
        SELECT (elem ->> 'chunk_id')::uuid AS cid,
               elem ->> 'text' AS txt,
               elem ->> 'source' AS src,
               (elem ->> 'doc_id')::uuid AS did,
               ord
        FROM jsonb_array_elements(fts_results) WITH ORDINALITY AS f(elem, ord)
    ),
    contributions AS (
        SELECT cid, 1.0::float8 / (rrf_k + ord) AS contrib FROM vec
        UNION ALL
        SELECT cid, 1.0::float8 / (rrf_k + ord) AS contrib FROM fts
    ),
    fused AS (
        SELECT cid, SUM(contrib) AS fscore
        FROM contributions
        GROUP BY cid
    ),
    attrs AS (
        SELECT DISTINCT ON (cid) cid, txt, src, did
        FROM (
            SELECT cid, txt, src, did, ord, 0 AS side FROM fts
            UNION ALL
            SELECT cid, txt, src, did, ord, 1 AS side FROM vec
        ) AS s
        ORDER BY cid, side ASC, ord DESC
    )
    SELECT a.cid, a.txt, a.src, a.did, fused.fscore
    FROM fused
    JOIN attrs a ON a.cid = fused.cid
    ORDER BY fused.fscore DESC, a.cid ASC
    LIMIT k
$$;
