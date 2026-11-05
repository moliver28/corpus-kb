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
 * One same-origin fetch, classified to the house result. Never throws,
 * never retries. An external `signal` aborts the same request. The
 * default wait cap comes from /app/runtime-config.json through
 * api/runtimeConfig.ts (the one config source); it is 60 s when the
 * config is absent or invalid.
 */
async function send<T>(
  path: string,
  init: RequestInit,
  signal: AbortSignal | undefined,
  timeoutMs: number | "none",
): Promise<OpResult<T>> {
  const ctrl = new AbortController();
  const timer = timeoutMs === "none" ? null : setTimeout(() => ctrl.abort(), timeoutMs);
  const onCallerAbort = (): void => ctrl.abort();
  if (signal?.aborted) {
    // A pre-aborted caller signal never fires its abort event.
    ctrl.abort();
  } else {
    signal?.addEventListener("abort", onCallerAbort);
  }
  try {
    // The SPA talks to its own origin only. Building the URL from the
    // page origin and refusing any other origin keeps every request
    // same-origin, whatever path string a caller passes.
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin) {
      return networkResult();
    }
    const res = await fetch(url, { ...init, signal: ctrl.signal });
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
    // The caller-signal listener leaves with the call, so a long-lived
    // AbortSignal never accumulates one listener per request.
    signal?.removeEventListener("abort", onCallerAbort);
  }
}

/**
 * Call one op and resolve to a typed OpResult. JSON body in, house
 * result out; the Content-Type header rides only on a body.
 */
export async function callOp<T>(
  path: string,
  opts: CallOpOptions = {},
): Promise<OpResult<T>> {
  const { method = "POST", body, signal, timeoutMs = requestTimeoutMs() } = opts;
  return send<T>(
    path,
    {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    signal,
    timeoutMs,
  );
}

/**
 * One multipart POST (the file upload route is not a JSON op) through
 * the same send path as callOp: same wait cap, same error model, one
 * fetch, no retry. The browser sets the multipart Content-Type with
 * its boundary, so no header is sent here.
 */
export function sendMultipart(
  path: string,
  form: FormData,
  opts: Pick<CallOpOptions, "signal" | "timeoutMs"> = {},
): Promise<OpResult<unknown>> {
  const { signal, timeoutMs = requestTimeoutMs() } = opts;
  return send<unknown>(path, { method: "POST", body: form }, signal, timeoutMs);
}
