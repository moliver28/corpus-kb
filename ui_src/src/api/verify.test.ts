import { describe, expect, it } from "vitest";
import { verifyBadge } from "./verify";
import { BADGE_THRESHOLDS } from "./thresholds.generated";

/**
 * Verify badge rows (spec D.4), mirroring the ported Python rows, plus
 * the missing-thresholds degrade path pinned by the program constraint.
 */

describe("verifyBadge", () => {
  it("null verify -> not verified", () => {
    expect(verifyBadge(null)).toMatchObject({ label: "not verified", degraded: false });
  });

  it("empty verify -> not verified", () => {
    expect(verifyBadge({})).toMatchObject({ label: "not verified", groundedness: 0 });
  });

  it("abstained true -> not verified even at groundedness 1.0", () => {
    const badge = verifyBadge({ groundedness: 1.0, abstained: true });
    expect(badge.label).toBe("not verified");
  });

  it("0.99 with the config threshold -> verified", () => {
    const badge = verifyBadge({ groundedness: BADGE_THRESHOLDS.verifyFullGroundedness }, BADGE_THRESHOLDS);
    expect(badge.label).toBe("verified");
  });

  it("0.989 -> partial", () => {
    expect(verifyBadge({ groundedness: 0.989 }, BADGE_THRESHOLDS).label).toBe("partial");
  });

  it("groundedness 0, abstained false -> partial", () => {
    expect(verifyBadge({ groundedness: 0, abstained: false }, BADGE_THRESHOLDS).label).toBe("partial");
  });

  it("missing thresholds -> not verified with the degraded note flag", () => {
    const badge = verifyBadge({ groundedness: 1 }, undefined);
    expect(badge.label).toBe("not verified");
    expect(badge.degraded).toBe(true);
  });

  it("non-numeric groundedness -> not verified, degraded (never a fake partial)", () => {
    const text = verifyBadge({ groundedness: "high" }, BADGE_THRESHOLDS);
    expect(text.label).toBe("not verified");
    expect(text.degraded).toBe(true);
    const nan = verifyBadge({ groundedness: Number.NaN }, BADGE_THRESHOLDS);
    expect(nan.degraded).toBe(true);
  });

  it("invalid thresholds (NaN, null) -> not verified, degraded", () => {
    expect(verifyBadge({ groundedness: 1 }, { verifyFullGroundedness: Number.NaN }).degraded).toBe(true);
    expect(verifyBadge({ groundedness: 1 }, null).degraded).toBe(true);
  });
});
