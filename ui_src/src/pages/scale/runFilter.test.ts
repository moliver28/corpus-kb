import { describe, expect, it } from "vitest";
import { filterRuns, projectOptions, runsOf } from "./runFilter";
import type { LedgerRun } from "./runFilter";

const A = "00000000-0000-0000-0000-00000000000a";
const B = "00000000-0000-0000-0000-00000000000b";

function run(project_id: string | null, run_id = "r1"): LedgerRun {
  return {
    run_id,
    status: "completed",
    started_at: "2026-10-27T00:00:00+00:00",
    source_type: "markdown",
    project_id,
  };
}

describe("runsOf", () => {
  it("reads the runs array out of a status payload", () => {
    const runs = runsOf({ runs: [run(A)], incomplete_runs: 1 });
    expect(runs).toHaveLength(1);
    expect(runs[0].project_id).toBe(A);
  });

  it("answers [] for drifted or missing payloads", () => {
    expect(runsOf(null)).toEqual([]);
    expect(runsOf({})).toEqual([]);
    expect(runsOf({ runs: ["nope", 42, null] })).toEqual([]);
  });

  it("keeps the honest no-project rows (migration 036 backfill rule)", () => {
    const runs = runsOf({ runs: [run(null)] });
    expect(runs).toHaveLength(1);
    expect(runs[0].project_id).toBeNull();
  });
});

describe("projectOptions", () => {
  it("lists distinct non-null project ids in first-seen order", () => {
    const runs = [run(A, "r1"), run(null, "r2"), run(B, "r3"), run(A, "r4")];
    expect(projectOptions(runs)).toEqual([A, B]);
  });

  it("answers [] when no run names a project", () => {
    expect(projectOptions([run(null, "r1")])).toEqual([]);
  });
});

describe("filterRuns", () => {
  const runs = [run(A, "r1"), run(null, "r2"), run(B, "r3")];

  it("passes everything through when no project is selected", () => {
    expect(filterRuns(runs, null)).toHaveLength(3);
  });

  it("narrows to one project", () => {
    expect(filterRuns(runs, A).map((r) => r.run_id)).toEqual(["r1"]);
  });

  it("never shows the no-project history under a project filter", () => {
    expect(filterRuns(runs, A).map((r) => r.run_id)).not.toContain("r2");
  });
});
