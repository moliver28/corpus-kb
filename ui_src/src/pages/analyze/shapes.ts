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

export interface AdvisorEnvelope {
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

