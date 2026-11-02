// Verify badge (spec D.4, port of ui/view_models.py::verify_badge).
// Thresholds come from thresholds.generated.ts — never a local literal.
import { BADGE_THRESHOLDS, type BadgeThresholds } from "./thresholds.generated";

type BadgeLabel = "verified" | "partial" | "not verified";

interface Badge {
  label: BadgeLabel;
  groundedness: number;
  /** True when thresholds were missing or invalid (renders with a note). */
  degraded: boolean;
}

/** The `verify` block the answer envelope carries (fields are opaque here). */
export interface VerifySummary {
  groundedness?: unknown;
  abstained?: unknown;
}

function validThresholds(t: BadgeThresholds | null | undefined): boolean {
  return (
    typeof t === "object" &&
    t !== null &&
    typeof t.verifyFullGroundedness === "number" &&
    Number.isFinite(t.verifyFullGroundedness)
  );
}

/**
 * One badge from a verify summary. Missing thresholds degrade to
 * "not verified" with degraded: true — never a crash, never a silent pass.
 */
export function verifyBadge(
  verify: VerifySummary | null | undefined,
  thresholds?: BadgeThresholds | null,
): Badge {
  const rec: VerifySummary = verify ?? {};
  const raw = rec.groundedness;
  const hasGroundedness = typeof raw === "number" && Number.isFinite(raw);
  const g = hasGroundedness ? raw : 0;
  if (!verify || Object.keys(verify).length === 0 || rec.abstained === true) {
    return { label: "not verified", groundedness: g, degraded: false };
  }
  // A present but non-numeric groundedness is unreadable evidence: the
  // badge degrades to "not verified" instead of a silent 0-based "partial".
  if (!hasGroundedness || !validThresholds(thresholds)) {
    return { label: "not verified", groundedness: g, degraded: true };
  }
  const full = thresholds?.verifyFullGroundedness ?? BADGE_THRESHOLDS.verifyFullGroundedness;
  return {
    label: g >= full ? "verified" : "partial",
    groundedness: g,
    degraded: false,
  };
}
