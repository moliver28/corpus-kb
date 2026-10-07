-- Rollback 018: drop the keyword-synthesis hit records (todo 17).
-- The keyword SETS themselves live on the event chain (KeywordSetUpdated)
-- and the Wave-1 code_keywords table — this rollback removes only the
-- operational hit-record table.

DROP TABLE IF EXISTS research_keyword_hits;
