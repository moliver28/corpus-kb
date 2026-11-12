// The SSE transport for one op (issue #131): the /api/ask stream variant.
// Same frame family the /v1 endpoint emits (chat.completion.chunk content
// frames), plus ONE house frame (object "ask.envelope") carrying the
// COMPLETE ask envelope as the settle receipt. A reply that is not an
// event stream (older server, proxy, error) falls back to the plain JSON
// envelope path, so a JSON answer keeps working unchanged.
import { networkResult, normalize, unreadableResult } from "./errors";
import { requestTimeoutMs } from "./runtimeConfig";
import type { OpResult } from "./types";

/** One SSE data payload after JSON.parse (shape checked at read time). */
interface StreamFrame {
  object?: unknown;
  envelope?: unknown;
  choices?: unknown;
}

export interface StreamResponseLike {
  status: number;
  headers?: { get: (name: string) => string | null };
  text: () => Promise<string>;
  body?: ReadableStream<Uint8Array> | null;
}

interface CallOpStreamOptions {
  method?: "POST";
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number | "none";
  /** Called with each grounded text piece as it arrives. */
  onDelta: (piece: string) => void;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function deltaText(frame: StreamFrame): string {
  if (!isRecord(frame) || !Array.isArray(frame.choices) || frame.choices.length === 0) {
    return "";
  }
  const first = frame.choices[0];
  if (!isRecord(first) || !isRecord(first.delta)) {
    return "";
  }
  return typeof first.delta.content === "string" ? first.delta.content : "";
}

/**
 * One SSE frame set from the byte stream. Frames split on a blank line;
 * only "data:" lines count (the wire contract of the /v1 + ask surfaces).
 */
async function readFrames(
  stream: ReadableStream<Uint8Array>,
  onDelta: (piece: string) => void,
): Promise<unknown> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let envelope: unknown = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    for (;;) {
      const end = buffer.indexOf("\n\n");
      if (end < 0) break;
      const raw = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const line = raw.trim();
      if (!line.startsWith("data: ")) continue;
      const payload = line.slice("data: ".length);
      if (payload === "[DONE]") return envelope;
      let frame: unknown;
      try {
        frame = JSON.parse(payload);
      } catch {
        continue;
      }
      if (!isRecord(frame)) continue;
      if (frame.object === "ask.envelope") {
        envelope = frame.envelope;
      } else {
        const piece = deltaText(frame);
        if (piece !== "") onDelta(piece);
      }
    }
  }
  return envelope;
}

/**
 * Call one op with streaming, resolving to the SAME OpResult the plain
 * JSON client resolves to. Never throws, never retries. Deltas land in
 * onDelta while the flight runs; the resolved receipt is the envelope.
 */
export async function callOpStream(
  path: string,
  opts: CallOpStreamOptions,
): Promise<OpResult<unknown>> {
  const { method = "POST", body, signal, timeoutMs = requestTimeoutMs(), onDelta } = opts;
  const ctrl = new AbortController();
  const timer = timeoutMs === "none" ? null : setTimeout(() => ctrl.abort(), timeoutMs);
  if (signal?.aborted) {
    ctrl.abort();
  } else {
    signal?.addEventListener("abort", () => ctrl.abort());
  }
  try {
    const url = new URL(path, window.location.origin);
    if (url.origin !== window.location.origin) {
      return networkResult();
    }
    const res = (await fetch(url, {
      method,
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })) as StreamResponseLike;
    const contentType = res.headers?.get?.("content-type") ?? "";
    if (!contentType.includes("text/event-stream")) {
      // The JSON fallback: exactly what the non-streaming call returns.
      const text = await res.text();
      let parsed: unknown = null;
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = null;
      }
      if (parsed === null) {
        return unreadableResult(res.status);
      }
      return normalize(res.status, parsed);
    }
    if (!res.body) {
      return unreadableResult(res.status);
    }
    const envelope = await readFrames(res.body, onDelta);
    if (envelope === null || envelope === undefined) {
      return unreadableResult(res.status);
    }
    return normalize(200, envelope);
  } catch {
    return networkResult();
  } finally {
    if (timer) clearTimeout(timer);
  }
}
