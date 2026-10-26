// Records real envelope responses from a RUNNING corpus-kb server into the
// committed fixture files (src/test/fixtures/). Run:
//   CORPUS_KB_BASE_URL=http://localhost:8010 node scripts/record-fixtures.ts
// Canonical fixtures (no live route yet) are never touched — flip their
// "source" to "recorded" and add a probe here once the route exists.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

interface Probe {
  name: string;
  method: string;
  path: string;
  file: string;
}

const here = dirname(fileURLToPath(import.meta.url));
const fixturesDir = resolve(here, "..", "src", "test", "fixtures");
const base = process.env["CORPUS_KB_BASE_URL"] ?? "http://localhost:8010";

const PROBES: Probe[] = [
  {
    name: "validation-error-400",
    method: "GET",
    path: "/api/release/status",
    file: "validation-error-400.json",
  },
  { name: "unavailable-503", method: "GET", path: "/api/documents", file: "unavailable-503.json" },
  { name: "server-error-500", method: "GET", path: "/api/stats", file: "server-error-500.json" },
  { name: "not-found-404", method: "POST", path: "/api/nope", file: "not-found-404.json" },
];

for (const probe of PROBES) {
  let response: Response;
  try {
    response = await fetch(base + probe.path, {
      method: probe.method,
      headers: { "Content-Type": "application/json" },
    });
  } catch {
    console.error(
      `Could not reach ${base}. Start the server first (corpus-kb start --transport http --port 8010).`,
    );
    process.exitCode = 1;
    break;
  }
  const text = await response.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    body = { raw: text };
  }
  const file = join(fixturesDir, probe.file);
  const previous = JSON.parse(readFileSync(file, "utf-8")) as Record<string, unknown>;
  const updated = {
    ...previous,
    name: probe.name,
    source: "recorded",
    request: { method: probe.method, path: probe.path },
    response: { httpStatus: response.status, body },
  };
  writeFileSync(file, JSON.stringify(updated, null, 2) + "\n");
  console.log(`recorded ${probe.name}: HTTP ${response.status} -> ${probe.file}`);
}
