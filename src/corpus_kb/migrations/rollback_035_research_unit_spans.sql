-- Rollback of 035_research_unit_spans: drop the nullable unit char spans.

ALTER TABLE research_units DROP COLUMN IF EXISTS start_char;
ALTER TABLE research_units DROP COLUMN IF EXISTS end_char;
