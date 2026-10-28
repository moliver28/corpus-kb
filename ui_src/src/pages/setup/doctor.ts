// Shapes for the GET /api/doctor envelope (tools/doctor_tools.py). The
// SPA reads only these fields; anything else stays in the JsonViewer.
// The typeguard keeps a drifted or unknown payload on the honest
// fallback path (review focus 5) instead of crashing the screen.
import { strings } from "../../strings";

/** One doctor probe row, exactly as the server sends it. */
export interface DoctorCheck {
  name: string;
  status: string;
  detail: string;
  fix: string;
  doc: string;
}

/** The result object inside the doctor envelope. */
export interface DoctorResult {
  profile: {
    profile: string;
    recommended_model: string;
  };
  postgres: {
    reachable: boolean;
    message: string;
    extensions: Record<string, { installed: boolean; version: string }> | null;
  };
  ollama: {
    reachable: boolean;
    message: string;
  };
  /** Structured embedding facts (issue #183); absent on older servers. */
  embedding?: {
    model: string;
    dimensions: number | null;
  };
  /** The reranker's active state from config; absent on older servers. */
  reranker?: {
    enabled: boolean;
    backend: string | null;
    model: string;
  };
  env: DoctorCheck;
  research_checks: DoctorCheck[];
  storage_checks: DoctorCheck[];
}

/** One table row for the detailed checks (stable key per position). */
export interface CheckRow {
  key: string;
  check: DoctorCheck;
}

function isCheck(value: unknown): value is DoctorCheck {
  if (typeof value !== "object" || value === null) return false;
  const c = value as Record<string, unknown>;
  return (
    typeof c.name === "string" &&
    typeof c.status === "string" &&
    typeof c.detail === "string" &&
    typeof c.fix === "string" &&
    typeof c.doc === "string"
  );
}

function isCheckList(value: unknown): value is DoctorCheck[] {
  return Array.isArray(value) && value.every(isCheck);
}

/** The `result` object of a house envelope, or undefined when absent. */
export function envelopeResult(data: unknown): unknown {
  if (typeof data === "object" && data !== null && "result" in data) {
    return (data as Record<string, unknown>).result;
  }
  return undefined;
}

/** True when the payload matches the doctor shape the screen renders. */
export function isDoctorResult(value: unknown): value is DoctorResult {
  if (typeof value !== "object" || value === null) return false;
  const r = value as Record<string, unknown>;
  const pg = r.postgres as Record<string, unknown> | undefined;
  const ol = r.ollama as Record<string, unknown> | undefined;
  const prof = r.profile as Record<string, unknown> | undefined;
  return (
    isCheck(r.env) &&
    isCheckList(r.research_checks) &&
    isCheckList(r.storage_checks) &&
    typeof prof === "object" &&
    prof !== null &&
    typeof prof.profile === "string" &&
    typeof prof.recommended_model === "string" &&
    typeof pg === "object" &&
    pg !== null &&
    typeof pg.reachable === "boolean" &&
    typeof pg.message === "string" &&
    typeof ol === "object" &&
    ol !== null &&
    typeof ol.reachable === "boolean" &&
    typeof ol.message === "string"
  );
}

/** Every check in the server's own order: env, research, storage. */
export function allChecks(result: DoctorResult): CheckRow[] {
  return [result.env, ...result.research_checks, ...result.storage_checks].map(
    (check, index) => ({ key: String(index), check }),
  );
}

// The server's probe enum words (ok/warn/fail/...) never render raw:
// each maps to one plain word; anything unknown reads as not checked.
const STATE_WORDS: Record<string, string> = {
  ok: strings.setupStateOk,
  warn: strings.setupStateWarn,
  fail: strings.setupStateFail,
  info: strings.setupStateInfo,
};

/** One plain word for a server probe status. */
export function stateWord(status: string): string {
  return STATE_WORDS[status] ?? strings.setupStateNotChecked;
}
