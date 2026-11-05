// Shapes for the conflicts family reads (Phase-2 Wave 3): the flag
// list and the scan summary in the Conflicts card. The SPA reads only
// these fields; anything else stays in the JsonViewer. The typeguards
// keep a drifted payload on the honest fallback path (OpBody drift
// view) instead of crashing the card. Source: conflicts/store.py.

/** One recorded conflict flag (conflicts/store.py LIST_SQL). */
export interface ConflictRow {
  conflict_id: string;
  statement_a_id: string;
  statement_b_id: string;
  anchor_name: string;
  kind: string;
  verdict: string;
  nli_verdict: string | null;
  resolved_verdict: string | null;
  resolved_by: string | null;
  created_at: string | null;
}

interface ConflictsEnvelope {
  status: string;
  conflicts: ConflictRow[];
  count: number;
}

export function isConflictsEnvelope(value: unknown): value is ConflictsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).conflicts;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.conflict_id === "string" &&
        typeof r.statement_a_id === "string" &&
        typeof r.statement_b_id === "string" &&
        typeof r.verdict === "string" &&
        (r.resolved_verdict === null || typeof r.resolved_verdict === "string") &&
        (r.resolved_by === null || typeof r.resolved_by === "string")
      );
    })
  );
}

/** One scan summary: pair counts plus the per-verdict tally. */
interface ScanEnvelope {
  status: string;
  scanned: number;
  flagged: number;
  verdicts: Record<string, number>;
}

export function isScanEnvelope(value: unknown): value is ScanEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.scanned === "number" &&
    Number.isFinite(v.scanned) &&
    typeof v.flagged === "number" &&
    Number.isFinite(v.flagged) &&
    typeof v.verdicts === "object" &&
    v.verdicts !== null
  );
}
