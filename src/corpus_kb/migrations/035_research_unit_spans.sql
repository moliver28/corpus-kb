-- Research-unit char spans (unified-ingestion Wave 3). One nullable span
-- per unit: (start_char, end_char) on the canonical transcript text the
-- research front-end builds (normalized turn texts joined by a blank line).
-- The offset invariant holds: text[start_char:end_char] == unit text.
-- Nullable on purpose: events recorded before this wave carry no spans, so
-- old rows (and their rebuilds) stay valid with NULL. No backfill.

ALTER TABLE research_units ADD COLUMN IF NOT EXISTS start_char BIGINT;
ALTER TABLE research_units ADD COLUMN IF NOT EXISTS end_char BIGINT;
