// Outcome classification (spec D.3): one envelope + HTTP status -> one Kind.
// User-facing words come from strings/en.json like every other surface.
import { strings } from "../strings";
import type { OpResult } from "./types";

const FAILED: ReadonlySet<string> = new Set(["error", "failed"]);

function asRecord(body: unknown): Record<string, unknown> {
  return typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
}

/**
 * Classify one JSON response body. Neutral statuses on HTTP 200 stay ok.
 * Unknown statuses surface as kind "unknown" with ok: true — never hidden.
 */
export function normalize<T>(httpStatus: number, body: unknown): OpResult<T> {
  const b = asRecord(body);
  const status = typeof b.status === "string" ? b.status : "";
  const base = {
    httpStatus,
    status,
    message: String(b.message ?? b.error ?? ""),
    remediation: String(b.remediation ?? ""),
    errorType: String(b.error_type ?? ""),
    data: b as T,
  };
  if (httpStatus === 503 || status === "unavailable") {
    return { ...base, ok: false, kind: "unavailable" };
  }
  if (httpStatus === 409 || status === "blocked") {
    return { ...base, ok: false, kind: "blocked" };
  }
  if (httpStatus >= 400 || FAILED.has(status)) {
    return { ...base, ok: false, kind: "error" };
  }
  if (status === "") {
    return { ...base, ok: true, kind: "unknown", status: "unknown" };
  }
  return { ...base, ok: true, kind: "ok" };
}

/** The body was missing or not JSON — the app cannot read this answer. */
export function unreadableResult(httpStatus: number): OpResult<never> {
  return {
    ok: false,
    kind: "unknown",
    httpStatus,
    status: "unknown",
    message: strings.unreadableResponse,
    remediation: "",
    errorType: "",
    data: null,
  };
}

/** Fetch itself failed (server down, DNS, abort, timeout). */
export function networkResult(): OpResult<never> {
  return {
    ok: false,
    kind: "network",
    httpStatus: 0,
    status: "network",
    message: strings.serverNotAnswering,
    remediation: strings.remediationRunDoctor,
    errorType: "",
    data: null,
  };
}

/** The op id is not in the manifest — a wiring bug, surfaced honestly. */
export function unknownOpResult(): OpResult<never> {
  return {
    ok: false,
    kind: "error",
    httpStatus: 0,
    status: "",
    message: strings.unknownOperation,
    remediation: "",
    errorType: "unknown_op",
    data: null,
  };
}
