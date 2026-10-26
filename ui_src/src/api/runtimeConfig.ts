// The ONE runtime-config source for the SPA (zero-drift rule 1): every
// consumer of /app/runtime-config.json reads the values through this
// module's cache. main.tsx loads it once at boot; later consumers (poll
// intervals, upload caps) call runtimeConfig() and get the same
// snapshot. A failed or invalid load keeps the built-in defaults — the
// SPA degrades to the pre-fetch behavior, never crashes. The defaults
// mirror corpus_kb.config's ui.spa.* defaults (config.py).

export interface RuntimeConfig {
  /** Milliseconds between status polls (ui.spa.poll_ms). */
  pollMs: number;
  /** Seconds one API call waits before giving up (ui.spa.request_timeout_s). */
  requestTimeoutS: number;
  /** Megabytes per uploaded file (ui.spa.max_upload_mb). */
  maxUploadMb: number;
  /** Server version for the stale-SPA refresh handshake. */
  serverVersion: string;
}

export const RUNTIME_CONFIG_URL = "/app/runtime-config.json";

/** Mirrors corpus_kb.config (SPA_*_DEFAULT); used when the fetch fails. */
const DEFAULTS: RuntimeConfig = {
  pollMs: 2000,
  requestTimeoutS: 60,
  maxUploadMb: 50,
  serverVersion: "",
};

let cached: RuntimeConfig = { ...DEFAULTS };
let loading: Promise<RuntimeConfig> | null = null;

/** The value bounds mirror the server-side ui.spa validation rules. */
function finiteBetween(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
}

/** Keep every valid key; an invalid key keeps its default (degrade path). */
export function applyRuntimeConfig(body: unknown): RuntimeConfig {
  if (typeof body !== "object" || body === null) {
    return cached;
  }
  const b = body as Record<string, unknown>;
  cached = {
    pollMs: finiteBetween(b.poll_ms, 100, 60_000) ? b.poll_ms : cached.pollMs,
    requestTimeoutS: finiteBetween(b.request_timeout_s, 0.1, 600)
      ? b.request_timeout_s
      : cached.requestTimeoutS,
    maxUploadMb: finiteBetween(b.max_upload_mb, 1, 2048) ? b.max_upload_mb : cached.maxUploadMb,
    serverVersion: typeof b.server_version === "string" ? b.server_version : cached.serverVersion,
  };
  return cached;
}

/** The current snapshot (defaults until the boot load lands). */
export function runtimeConfig(): RuntimeConfig {
  return cached;
}

/** The callOp wait cap in ms: the runtime value, else the 60 s default. */
export function requestTimeoutMs(): number {
  return Math.round(runtimeConfig().requestTimeoutS * 1000);
}

/**
 * Fetch runtime-config.json once and cache it; repeat calls reuse the
 * same promise. Never throws: any failure keeps the defaults.
 */
export function loadRuntimeConfig(): Promise<RuntimeConfig> {
  loading ??= fetch(RUNTIME_CONFIG_URL)
    .then(async (res) => applyRuntimeConfig(await res.json()))
    .catch(() => cached);
  return loading;
}
