// The one HTTP client every surface calls through (spec D.3).
import { networkResult, normalize, unreadableResult } from "./errors";
import type { OpResult } from "./types";

export interface CallOpOptions {
  method?: "GET" | "POST" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
  /** Abort the WAIT after this many ms; "none" waits for the server. */
  timeoutMs?: number | "none";
}

const DEFAULT_TIMEOUT_MS = 60_000;

/**
 * Call one op and resolve to a typed OpResult. Never throws, never retries
 * a POST automatically. An external `signal` aborts the same request.
 */
export async function callOp<T>(
  path: string,
  opts: CallOpOptions = {},
): Promise<OpResult<T>> {
  const { method = "POST", body, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = opts;
  const ctrl = new AbortController();
  const timer = timeoutMs === "none" ? null : setTimeout(() => ctrl.abort(), timeoutMs);
  signal?.addEventListener("abort", () => ctrl.abort());
  try {
    const res = await fetch(path, {
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
