-- ============================================================================
-- Migration 043: the project_tables registry (Track C, owner ruling)
-- ============================================================================
-- Static catalogue of the runtime raw_<slug> tables that
-- storage/project_tables.py creates (the ONE sanctioned dynamic-DDL seam,
-- zero-drift program 2026-11-06). Every runtime table MUST have one row
-- here; schema-parity, doctor, backup, and migrate-data enumerate the
-- registry, so a table without a row is an orphan and a row without a
-- table is an honest gap.
--
-- project_id follows the 036/037 precedent: a PLAIN nullable column, no
-- FK (the research_projects read model is projection-owned).
--
-- The raw tables themselves are runtime DDL and are NOT migration files.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS +
-- DROP POLICY IF EXISTS. This file is static DDL; the sanctioned dynamic
-- seam lives only in storage/project_tables.py.
-- ============================================================================

CREATE TABLE IF NOT EXISTS project_tables (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    project_id UUID,
    table_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, table_name)
);

-- ----------------------------------------------------------------------------
-- RLS: tenant isolation on the registry (migration 016/022 policy pattern,
-- ENABLE + FORCE, DROP POLICY IF EXISTS for re-apply)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['project_tables'] LOOP
        BEGIN
            EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I_tenant_isolation ON %I', t, t);
            EXECUTE format(
                'CREATE POLICY %I_tenant_isolation ON %I '
                'USING (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID) '
                'WITH CHECK (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::UUID)',
                t, t);
            EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        EXCEPTION
            WHEN duplicate_table THEN NULL;
            WHEN undefined_table THEN NULL;
        END;
    END LOOP;
END
$$;
