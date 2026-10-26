-- DuckDB twin of pg migration 035 (research unit char spans, Wave 3).
-- Same two nullable BIGINT columns; DuckDB has no RLS and no backfill need
-- (spans ride the events, so old rows stay NULL).

ALTER TABLE research_units ADD COLUMN IF NOT EXISTS start_char BIGINT;
ALTER TABLE research_units ADD COLUMN IF NOT EXISTS end_char BIGINT;
