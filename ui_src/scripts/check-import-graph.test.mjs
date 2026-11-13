// Negative tests for the import-graph gate (Wave 6 fix wave, the
// review's blocking finding: a gate with no negative test is not a
// gate). Fixture trees live only in the temp dir — never in the repo,
// so neither the production scan nor the bundle can see them. The
// arms prove the gate FAILS on a map-library import outside the lazy
// Map module (static, dagre, dynamic), PASSES a clean tree, reports
// an import-free scan as a lying scan, and that ALLOWED_PREFIXES is
// the load-bearing config: a widened list would silently pass the
// leak, so the production list is pinned exactly.
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { ALLOWED_PREFIXES, scanSource, walkClosure } from "./check-import-graph.mjs";

const XYFLOW = `import { ReactFlow } from "@xyflow/react";\nexport default function Badge() {\n  return null;\n}\n`;
const DAGRE = `import dagre from "@dagrejs/dagre";\nexport const lay = () => dagre;\n`;
const DYNAMIC = `export async function loadFlow() {\n  return import("@xyflow/react");\n}\n`;
const PLAIN = `export const plain = "no map library here";\n`;

let root = "";

/** One fixture tree under a fresh temp root; rel paths use forward slashes. */
function makeTree(files) {
  root = mkdtempSync(join(tmpdir(), "ckb-gate-"));
  for (const [rel, body] of Object.entries(files)) {
    mkdirSync(join(root, rel, ".."), { recursive: true });
    writeFileSync(join(root, rel), body);
  }
  return root;
}

afterEach(() => {
  if (root !== "") {
    rmSync(root, { recursive: true, force: true });
    root = "";
  }
});

describe("import-graph gate: source scan", () => {
  it("passes a clean tree whose only map import sits in pages/map/", () => {
    const dir = makeTree({
      "pages/Home.tsx": PLAIN,
      "pages/map/MapCanvas.tsx": XYFLOW,
    });
    const { offenders, importsSeen } = scanSource(dir);
    expect(offenders).toEqual([]);
    expect(importsSeen).toBe(1);
  });

  it("fails an @xyflow/react import outside the Map module", () => {
    const dir = makeTree({
      "components/FlowBadge.tsx": XYFLOW,
      "pages/map/MapCanvas.tsx": XYFLOW,
    });
    expect(scanSource(dir).offenders).toEqual(["components/FlowBadge.tsx"]);
  });

  it("fails an @dagrejs/dagre import outside the Map module", () => {
    const dir = makeTree({ "components/GraphViz.tsx": DAGRE });
    expect(scanSource(dir).offenders).toEqual(["components/GraphViz.tsx"]);
  });

  it("fails a dynamic import outside the Map module", () => {
    const dir = makeTree({ "components/LazyFlow.tsx": DYNAMIC });
    expect(scanSource(dir).offenders).toEqual(["components/LazyFlow.tsx"]);
  });

  it("answers zero imports as a lying scan, not a clean tree", () => {
    const dir = makeTree({ "pages/Home.tsx": PLAIN });
    expect(scanSource(dir).importsSeen).toBe(0);
  });

  it("a widened allowlist would pass the leak, so the real list is pinned", () => {
    const dir = makeTree({ "components/FlowBadge.tsx": XYFLOW });
    // The mutation arm: allow everything and the offender goes quiet —
    // exactly the silent pass the gate must never do.
    expect(scanSource(dir, { allowedPrefixes: [""] }).offenders).toEqual([]);
    // The guard: the production allowlist stays the lazy Map set, so a
    // widened-config change fails this line instead of passing a leak.
    expect(ALLOWED_PREFIXES).toEqual(["pages/Map.tsx", "pages/map/"]);
  });
});

describe("import-graph gate: entry closure walk", () => {
  it("walks static chunk imports transitively", () => {
    const deps = {
      "entry.js": ["vendor.js"],
      "vendor.js": ["shared.js"],
      "map.js": [],
    };
    const route = walkClosure("entry.js", (name) => deps[name] ?? []);
    expect([...route].sort()).toEqual(["entry.js", "shared.js", "vendor.js"]);
  });

  it("a leaked map chunk in the closure is visible to the caller", () => {
    const deps = { "entry.js": ["map.js"], "map.js": [] };
    const route = walkClosure("entry.js", (name) => deps[name] ?? []);
    expect(route.has("map.js")).toBe(true);
  });
});
