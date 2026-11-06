-- Rollback (DuckDB) for 018: drop the keyword-synthesis hit records.
-- Pair of migrations/rollback_018_keyword_synthesis.sql: one drop.

DROP TABLE IF EXISTS research_keyword_hits;
