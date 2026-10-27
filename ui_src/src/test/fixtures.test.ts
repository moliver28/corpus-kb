import { describe, expect, it } from "vitest";
import { normalize } from "../api/errors";
import askDegraded200 from "./fixtures/ask-degraded-200.json";
import askEmpty200 from "./fixtures/ask-empty-200.json";
import askSuccess200 from "./fixtures/ask-success-200.json";
import assignmentsOk200 from "./fixtures/assignments-ok-200.json";
import blocked409 from "./fixtures/blocked-409.json";
import degraded200 from "./fixtures/degraded-200.json";
import demoLoaded200 from "./fixtures/demo-loaded-200.json";
import demoPartial200 from "./fixtures/demo-partial-200.json";
import doctorOk200 from "./fixtures/doctor-ok.json";
import empty200 from "./fixtures/empty-200.json";
import ingestStatusOk200 from "./fixtures/ingest-status-ok-200.json";
import notebookAnswer200 from "./fixtures/notebook-answer-200.json";
import notFound404 from "./fixtures/not-found-404.json";
import ok200 from "./fixtures/ok-200.json";
import reembedOk200 from "./fixtures/reembed-ok-200.json";
import reviewExecuteOk200 from "./fixtures/review-execute-ok-200.json";
import searchOk200 from "./fixtures/search-ok-200.json";
import serverError500 from "./fixtures/server-error-500.json";
import topicsOk200 from "./fixtures/topics-ok-200.json";
import unavailable503 from "./fixtures/unavailable-503.json";
import validationError400 from "./fixtures/validation-error-400.json";

/**
 * Fixture drift gate: every committed fixture validates against the
 * declared envelope type (typeguard), and the client normalizer maps it
 * to the kind its name promises. A server payload-shape change breaks the
 * typeguard here, so drift fails ui-test.
 */

interface Envelope {
  status?: unknown;
  message?: unknown;
  error?: unknown;
  remediation?: unknown;
  error_type?: unknown;
}

interface FixtureFile {
  name: string;
  source: string;
  request: { method: string; path: string };
  response: { httpStatus: number; body: unknown };
}

function isEnvelope(body: unknown): body is Envelope {
  if (typeof body !== "object" || body === null) return false;
  const b = body as Record<string, unknown>;
  // The FastAPI default 404 body carries no status word — it still counts.
  if ("status" in b && typeof b.status !== "string") return false;
  for (const key of ["message", "error", "remediation", "error_type"] as const) {
    if (key in b && typeof b[key] !== "string") return false;
  }
  return true;
}

function isFixture(value: unknown): value is FixtureFile {
  if (typeof value !== "object" || value === null) return false;
  const f = value as Record<string, unknown>;
  if (typeof f.name !== "string" || typeof f.source !== "string") return false;
  if (f.source !== "recorded" && f.source !== "canonical") return false;
  const req = f.request as Record<string, unknown> | undefined;
  const res = f.response as Record<string, unknown> | undefined;
  return (
    typeof req === "object" && req !== null && typeof req.method === "string" &&
    typeof req.path === "string" &&
    typeof res === "object" && res !== null && typeof res.httpStatus === "number" &&
    isEnvelope(res.body)
  );
}

const FIXTURES = [
  validationError400,
  unavailable503,
  serverError500,
  notFound404,
  ok200,
  empty200,
  degraded200,
  blocked409,
  doctorOk200,
  demoLoaded200,
  demoPartial200,
  assignmentsOk200,
  topicsOk200,
  ingestStatusOk200,
  reembedOk200,
  askSuccess200,
  askEmpty200,
  askDegraded200,
  searchOk200,
  notebookAnswer200,
  reviewExecuteOk200,
] as unknown[];

/** name -> the kind the normalizer must produce for that fixture. */
const EXPECTED_KINDS: Record<string, string> = {
  "validation-error-400": "error",
  "unavailable-503": "unavailable",
  "server-error-500": "error",
  "not-found-404": "error",
  "ok-200": "ok",
  "empty-200": "ok",
  "degraded-200": "ok",
  "blocked-409": "blocked",
  "doctor-ok-200": "ok",
  "demo-loaded-200": "ok",
  "demo-partial-200": "ok",
  "assignments-ok-200": "ok",
  "topics-ok-200": "ok",
  "ingest-status-ok-200": "ok",
  "reembed-ok-200": "ok",
  "ask-success-200": "ok",
  "ask-empty-200": "ok",
  "ask-degraded-200": "ok",
  "search-ok-200": "ok",
  "notebook-answer-200": "ok",
  "review-execute-ok-200": "ok",
};

describe("committed envelope fixtures", () => {
  it.each(FIXTURES.map((f) => [f]))("fixture validates against the envelope type", (fixture) => {
    expect(isFixture(fixture)).toBe(true);
  });

  it.each(FIXTURES.map((f) => [f]))(
    "fixture normalizes to its promised kind",
    (fixture) => {
      expect(isFixture(fixture)).toBe(true);
      const f = fixture as FixtureFile;
      const result = normalize(f.response.httpStatus, f.response.body);
      expect(result.kind).toBe(EXPECTED_KINDS[f.name]);
      if (
        f.name === "ok-200" ||
        f.name === "empty-200" ||
        f.name === "degraded-200" ||
        f.name === "doctor-ok-200" ||
        f.name === "demo-loaded-200" ||
        f.name === "demo-partial-200" ||
        f.name === "assignments-ok-200" ||
        f.name === "topics-ok-200" ||
        f.name === "ingest-status-ok-200" ||
        f.name === "reembed-ok-200" ||
        f.name === "ask-success-200" ||
        f.name === "ask-empty-200" ||
        f.name === "ask-degraded-200" ||
        f.name === "search-ok-200" ||
        f.name === "notebook-answer-200" ||
        f.name === "review-execute-ok-200"
      ) {
        expect(result.ok).toBe(true);
      } else {
        expect(result.ok).toBe(false);
      }
    },
  );
});
