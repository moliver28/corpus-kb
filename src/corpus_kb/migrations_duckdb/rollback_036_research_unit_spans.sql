-- Rollback of 036_research_unit_spans: drop the nullable unit char spans.
--
-- DuckDB refuses any DROP COLUMN on a table that carries an index, so
-- the 016 index leaves first and returns after the columns drop
-- (issue #592 item 1 pin surfaced this).

DROP INDEX IF EXISTS idx_research_units_doc;

ALTER TABLE research_units DROP COLUMN IF EXISTS start_char;
ALTER TABLE research_units DROP COLUMN IF EXISTS end_char;

CREATE INDEX IF NOT EXISTS idx_research_units_doc
    ON research_units(tenant_id, doc_id, seq);
