// Shapes for the beliefs family reads (Phase-2 Wave 3): the Beliefs
// card's current, timeline, predict, and compare views. The SPA reads
// only these fields; anything else stays in the JsonViewer. The
// typeguards keep a drifted payload on the honest fallback path (OpBody
// drift view) instead of crashing the card. Sources: beliefs/store.py,
// tools/beliefs_tools.py.

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
  return Array.isArray(rows) && rows.every(isBeliefRowShape);
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
interface PosteriorShape {
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
