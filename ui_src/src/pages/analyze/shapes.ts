// Shapes for the Analyze screen reads. The SPA reads only these fields;
// anything else stays in the JsonViewer. The typeguards keep a drifted
// or unknown payload on the honest fallback path (DoctorCard pattern).

/** One ranked register row (analysis/method_register.py). */
export interface AdvisorRow {
  id: string;
  name: string;
  summary: string;
  complexity_tier: string;
  rank: number;
  rank_reason: string;
  /** The manifest op that runs this method, snake_case; null = human-only. */
  op: string | null;
  validation_required: unknown;
  permitted_claims: unknown;
  do_not_claim: unknown;
}

interface AdvisorEnvelope {
  status: string;
  recommendations: AdvisorRow[];
}

function optionalText(value: unknown): boolean {
  return value === undefined || typeof value === "string";
}

export function isAdvisorEnvelope(value: unknown): value is AdvisorEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.status !== "string" || v.status === "") return false;
  if (!Array.isArray(v.recommendations)) return false;
  return v.recommendations.every((row) => {
    if (typeof row !== "object" || row === null) return false;
    const r = row as Record<string, unknown>;
    return (
      typeof r.id === "string" &&
      typeof r.name === "string" &&
      typeof r.summary === "string" &&
      typeof r.rank === "number" &&
      (r.op === null || typeof r.op === "string") &&
      optionalText(r.validation_required) &&
      optionalText(r.permitted_claims) &&
      optionalText(r.do_not_claim)
    );
  });
}

/** One guidance cell -> "" when absent or unreadable (never [object Object]). */
export function textOf(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/**
 * The register names its op in snake_case ("keyphrases_extract"); the
 * manifest ids are kebab-case ("keyphrases-extract").
 */
export function manifestOpId(registerOp: string): string {
  return registerOp.replace(/_/g, "-");
}


// -- Question routing (analysis routing program, wave 3) -------------------

/** One ranked routing row (tools/analysis_route_tools.py). */
export interface RouteRanked {
  method_id: string;
  name: string;
  why: string;
  /** The manifest op that runs this method, snake_case; null = human-only. */
  run_hint: { op: string | null; params: unknown };
}

/** One restatement from the ambiguity ladder (analysis/question_intent.py). */
export interface RouteReading {
  method_id: string;
  method_name: string;
  why: string;
}

/** One trace score row (analysis/question_router.py RoutingTrace). */
export interface RouteScore {
  method_id: string;
  score: number;
}

interface RouteIntentShape {
  status?: unknown;
  note?: unknown;
}

interface RouteTraceShape {
  mode?: unknown;
  threshold?: unknown;
  margin?: unknown;
  scores?: unknown;
}

/** The analysis_route envelope; conditional keys stay optional here. */
export interface RouteEnvelope {
  status: string;
  intent: RouteIntentShape;
  ranked: RouteRanked[];
  /** Null rides as absent (the guard lets it through). */
  interpretations?: RouteReading[] | null;
  trace?: RouteTraceShape;
}

function isRouteRanked(value: unknown): boolean {
  if (typeof value !== "object" || value === null) return false;
  const r = value as Record<string, unknown>;
  const hint = r.run_hint;
  const hintOk =
    typeof hint === "object" &&
    hint !== null &&
    ((hint as Record<string, unknown>).op === null ||
      typeof (hint as Record<string, unknown>).op === "string");
  return (
    typeof r.method_id === "string" &&
    typeof r.name === "string" &&
    typeof r.why === "string" &&
    hintOk
  );
}

function isRouteReading(value: unknown): boolean {
  if (typeof value !== "object" || value === null) return false;
  const r = value as Record<string, unknown>;
  return (
    typeof r.method_id === "string" &&
    typeof r.method_name === "string" &&
    typeof r.why === "string"
  );
}

export function isRouteEnvelope(value: unknown): value is RouteEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.status !== "string" || v.status === "") return false;
  if (!Array.isArray(v.ranked) || !v.ranked.every(isRouteRanked)) return false;
  if (v.interpretations !== undefined && v.interpretations !== null) {
    if (!Array.isArray(v.interpretations) || !v.interpretations.every(isRouteReading)) {
      return false;
    }
  }
  return true;
}

/** The trace's score rows, or [] when absent or unreadable. */
export function routeScores(trace: RouteTraceShape | undefined): RouteScore[] {
  const rows = trace?.scores;
  if (!Array.isArray(rows)) return [];
  return rows.filter(
    (row): row is RouteScore =>
      typeof row === "object" &&
      row !== null &&
      typeof (row as Record<string, unknown>).method_id === "string" &&
      typeof (row as Record<string, unknown>).score === "number",
  );
}

/** The intent note ("degraded" | "expert_override"), or "" when absent. */
export function routeNote(envelope: RouteEnvelope): string {
  return textOf(envelope.intent?.note);
}
