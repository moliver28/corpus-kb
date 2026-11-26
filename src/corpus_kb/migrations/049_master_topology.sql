-- ============================================================================
-- Migration 049: master topology (W5, spec s3)
-- ============================================================================
-- Workspace attribution and the derived meta index view. The slug names a
-- project folder under master.workspace_root; NULL slugs stay distinct
-- (a UNIQUE index ignores rows whose keyed column is NULL). Additive only:
-- no destructive step, so no rollback mirror (P2 plan W5-3).

ALTER TABLE research_projects ADD COLUMN IF NOT EXISTS slug TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_research_projects_slug
    ON research_projects (tenant_id, slug);

-- No security_invoker on the view: today every base table is FORCE RLS
-- with the session GUC, the view stays tenant-scoped. If a future
-- migration feeds it a non-forced table, add security_invoker = true.
CREATE OR REPLACE VIEW master_project_index AS
SELECT p.tenant_id,
       p.project_id,
       p.name,
       p.slug,
       (SELECT COUNT(*) FROM documents d
         WHERE d.tenant_id = p.tenant_id AND d.project_id = p.project_id) AS origin_doc_count,
       (SELECT COUNT(*) FROM project_claims c
         WHERE c.tenant_id = p.tenant_id AND c.project_id = p.project_id
           AND c.source_project_id <> p.project_id) AS claim_doc_count,
       (SELECT MAX(a.last_ts) FROM project_activity a
         WHERE a.tenant_id = p.tenant_id AND a.project_id = p.project_id) AS last_activity_ts
FROM research_projects p;
