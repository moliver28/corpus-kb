// Pure helpers for the run-ledger project filter (SPA ingest program).
// The ledger rows come from GET ingest-status (`runs`); each row carries
// the nullable project_id (migration 036). Rows without a project show
// under "All projects" only — the honest "no project" history.

/** One ingest_runs row, exactly as ingest-status serializes it. */
export interface LedgerRun {
  run_id: string;
  status: string;
  started_at: string | null;
  source_type: string | null;
  project_id: string | null;
}

/** The `runs` array of a status payload, or [] when the shape drifted. */
export function runsOf(data: unknown): LedgerRun[] {
  if (typeof data !== "object" || data === null) return [];
  const runs = (data as Record<string, unknown>).runs;
  if (!Array.isArray(runs)) return [];
  return runs.filter((row): row is LedgerRun => {
    if (typeof row !== "object" || row === null) return false;
    const r = row as Record<string, unknown>;
    return (
      typeof r.run_id === "string" &&
      typeof r.status === "string" &&
      (r.started_at === null || typeof r.started_at === "string") &&
      (r.source_type === null || typeof r.source_type === "string") &&
      (r.project_id === null || typeof r.project_id === "string")
    );
  });
}

/** Distinct non-null project ids, stable order (first-seen). */
export function projectOptions(runs: LedgerRun[]): string[] {
  const seen: string[] = [];
  for (const run of runs) {
    if (run.project_id !== null && !seen.includes(run.project_id)) {
      seen.push(run.project_id);
    }
  }
  return seen;
}

/** The runs of one project; null selects every run. */
export function filterRuns(runs: LedgerRun[], project: string | null): LedgerRun[] {
  if (project === null) return runs;
  return runs.filter((run) => run.project_id === project);
}
