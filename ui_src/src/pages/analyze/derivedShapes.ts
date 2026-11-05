// Shapes for the derived-analysis family reads (Phase-2 Wave 3): the
// statements, conflicts, and beliefs cards plus the corpus stats strip.
// The SPA reads only these fields; anything else stays in the JsonViewer.
// The typeguards keep a drifted payload on the honest fallback path
// (OpBody drift view) instead of crashing the card. Sources:
// statements/store.py, conflicts/store.py, beliefs/store.py,
// tools/versioning_tools.py.

/** One statement atom (statements/store.py LIST_SQL / TIMELINE_SQL). */
export interface StatementRow {
  statement_id: string;
  text: string;
  template_id: string;
  valid_at: string | null;
  observed_at: string | null;
  confidence: number | null;
  superseded_by: string | null;
}

interface StatementsEnvelope {
  status: string;
  statements: StatementRow[];
  count: number;
}

export function isStatementsEnvelope(value: unknown): value is StatementsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).statements;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.statement_id === "string" &&
        typeof r.text === "string" &&
        typeof r.template_id === "string" &&
        (r.valid_at === null || typeof r.valid_at === "string") &&
        (r.observed_at === null || typeof r.observed_at === "string") &&
        (r.superseded_by === null || typeof r.superseded_by === "string")
      );
    })
  );
}

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

/** One current belief posterior (beliefs/store.py CURRENT_SQL). */
export interface BeliefRow {
  belief_id: string;
  subject: string;
  relation: string;
  object: string;
  alpha: number;
  beta: number;
  evidence_count: number;
  era_count: number;
  updated_at: string | null;
}

interface BeliefsEnvelope {
  status: string;
  beliefs: BeliefRow[];
  count: number;
}

export function isBeliefsEnvelope(value: unknown): value is BeliefsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).beliefs;
  return (
    Array.isArray(rows) &&
    rows.every(isBeliefRowShape) &&
    rows.length >= 0
  );
}

interface CompareEnvelope {
  status: string;
  subject: string;
  rows: BeliefRow[];
  count: number;
}

/** The compare answer carries its rows under ``rows`` (beliefs_compare). */
export function isCompareEnvelope(value: unknown): value is CompareEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).rows;
  return Array.isArray(rows) && rows.every(isBeliefRowShape);
}

/** One belief-row shape check shared by the current and compare guards. */
function isBeliefRowShape(row: unknown): boolean {
  if (typeof row !== "object" || row === null) return false;
  const r = row as Record<string, unknown>;
  return (
    typeof r.belief_id === "string" &&
    typeof r.subject === "string" &&
    typeof r.relation === "string" &&
    typeof r.object === "string" &&
    typeof r.alpha === "number" &&
    Number.isFinite(r.alpha) &&
    typeof r.beta === "number" &&
    Number.isFinite(r.beta)
  );
}

/** One era segment of a belief timeline (beliefs/store.py timeline). */
export interface EraRow {
  start: string;
  end: string;
  positives: number;
  negatives: number;
  mean: number;
}

/** The stored posterior block of a timeline answer. */
export interface PosteriorShape {
  alpha: number;
  beta: number;
  mean: number;
  interval_low: number;
  interval_high: number;
  evidence_count: number;
  era_count: number;
}

interface TimelineEnvelope {
  status: string;
  belief_id: string;
  eras: EraRow[];
  posterior: PosteriorShape;
}

export function isTimelineEnvelope(value: unknown): value is TimelineEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.belief_id !== "string" || !Array.isArray(v.eras)) return false;
  if (typeof v.posterior !== "object" || v.posterior === null) return false;
  const p = v.posterior as Record<string, unknown>;
  return (
    typeof p.mean === "number" &&
    Number.isFinite(p.mean) &&
    typeof p.interval_low === "number" &&
    typeof p.interval_high === "number"
  );
}

/** One posterior-predictive answer (tools/beliefs_tools.py beliefs_predict). */
interface PredictEnvelope {
  status: string;
  probability: number;
  interval_low: number;
  interval_high: number;
  evidence_count: number;
}

export function isPredictEnvelope(value: unknown): value is PredictEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.probability === "number" &&
    Number.isFinite(v.probability) &&
    typeof v.interval_low === "number" &&
    typeof v.interval_high === "number"
  );
}

/** One corpus stats answer: one row count per key. */
interface StatsEnvelope {
  status: string;
  stats: Record<string, number>;
}

export function isStatsEnvelope(value: unknown): value is StatsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const stats = (value as Record<string, unknown>).stats;
  if (typeof stats !== "object" || stats === null) return false;
  return Object.values(stats).every((n) => typeof n === "number" && Number.isFinite(n));
}

/** One per-source-type count row (versioning_handler.py document stats). */
export interface SourceTypeRow {
  source_type: string;
  count: number;
}

interface DocumentStatsEnvelope {
  status: string;
  by_source_type: SourceTypeRow[];
}

export function isDocumentStatsEnvelope(value: unknown): value is DocumentStatsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).by_source_type;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.source_type === "string" &&
        typeof r.count === "number" &&
        Number.isFinite(r.count)
      );
    })
  );
}
