-- ============================================================================
-- Rollback for migration 024 (rrf fusion deterministic tie order)
-- ============================================================================
-- Restores the migration 006 function body verbatim (ties ordered by first
-- appearance in the input arrays). Re-applying migration 024 brings the
-- chunk-id tiebreak back. Nothing else is touched: the function owns no
-- tables and its input/output shape is unchanged between 006 and 024.
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
    first_seen AS (
        SELECT cid, MIN(appear) AS first_appearance
        FROM (
            SELECT cid, ord AS appear FROM vec
            UNION ALL
            SELECT cid, (SELECT COUNT(*) FROM vec) + ord AS appear FROM fts
        ) AS s
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
    JOIN first_seen fs ON fs.cid = fused.cid
    ORDER BY fused.fscore DESC, fs.first_appearance ASC
    LIMIT k
$$;
