// Shapes for the verify op read. The SPA reads only these fields;
// anything else stays in the JsonViewer. The typeguards keep a drifted
// or unknown payload on the honest fallback path (shapes.ts pattern)
// instead of crashing the screen.

/** One verdict row (domain/models.py ClaimVerdict). */
export interface ClaimVerdictRow {
  claim: string;
  label: string;
  confidence: unknown;
}

interface VerifyResult {
  verdicts: ClaimVerdictRow[];
  groundedness: unknown;
  abstained: unknown;
}

/** The verify envelope (routes_search.py verify: {status, result}). */
export interface VerifyEnvelope {
  status: string;
  result: VerifyResult;
}

export function isVerifyEnvelope(value: unknown): value is VerifyEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.status !== "string" || v.status === "") return false;
  if (typeof v.result !== "object" || v.result === null) return false;
  const r = v.result as Record<string, unknown>;
  if (!Array.isArray(r.verdicts)) return false;
  return r.verdicts.every((row) => {
    if (typeof row !== "object" || row === null) return false;
    const c = row as Record<string, unknown>;
    return typeof c.claim === "string" && typeof c.label === "string";
  });
}

/** True only on the server's explicit abstain word. */
export function abstainedOf(env: VerifyEnvelope): boolean {
  return env.result.abstained === true;
}
