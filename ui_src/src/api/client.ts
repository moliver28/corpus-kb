// The one HTTP client every surface calls through (spec D.3).
import { networkResult, normalize, unreadableResult } from "./errors";
import { requestTimeoutMs } from "./runtimeConfig";
import type { OpResult } from "./types";

interface CallOpOptions {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** Abort the WAIT after this many ms; "none" waits for the server. */
  timeoutMs?: number | "none";
}

/**
 * Call one op and resolve to a typed OpResult. Never throws, never retries
 * a POST automatically. An external `signal` aborts the same request.
 * The default wait cap comes from /app/runtime-config.json through
 * api/runtimeConfig.ts (the one config source); it is 60 s when the
 * config is absent or invalid.
 */
export async function callOp<T>(
  path: string,
  opts: CallOpOptions = {},
): Promise<OpResult<T>> {
  const { method = "POST", body, signal, timeoutMs = requestTimeoutMs() } = opts;
  const ctrl = new AbortController();
  const timer = timeoutMs === "none" ? null : setTimeout(() => ctrl.abort(), timeoutMs);
  if (signal?.aborted) {
    // A pre-aborted caller signal never fires its abort event.
    ctrl.abort();
  } else {
    signal?.addEventListener("abort", () => ctrl.abort());
  }
  try {
    // The SPA talks to its own origin only. Building the URL from the
    // page origin and refusing any other origin keeps every request
    // same-origin, whatever path string a caller passes.
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin) {
      return networkResult();
    }
    const res = await fetch(url, {
      method,
      signal: ctrl.signal,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await res.text();
    let parsed: unknown = null;
    try {
      parsed = JSON.parse(text);
    } catch {
      // non-JSON body — handled by the unreadable result below
    }
    if (parsed === null) {
      return unreadableResult(res.status);
    }
    return normalize<T>(res.status, parsed);
  } catch {
    return networkResult();
  } finally {
    if (timer) clearTimeout(timer);
  }
}
