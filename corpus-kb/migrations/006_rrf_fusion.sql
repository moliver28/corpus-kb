-- Reciprocal Rank Fusion in pure SQL (idempotent — CREATE OR REPLACE).
-- corpus.rrf_fusion() replaces the Python RRF loop formerly in
-- QueryHandler.handle_search and mirrors it exactly:
--   * each side arrives pre-sorted by its own score; rank is the 1-based
--     array position (WITH ORDINALITY == Python's enumerate + 1),
--   * fused score = SUM(1.0 / (rrf_k + rank)) across both sides in float8,
--     matching Python double arithmetic,
--   * metadata (text/source/doc_id) follows Python's dict-overwrite order:
--     the FTS side wins when a chunk appears in both,
--   * ties on fused score keep Python's stable-sort first-seen order
--     (vector hits by rank, then FTS-only hits by rank).
-- No extension required. NULL side behaves like an empty array.
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
