// Shared test doubles for the SPA suite: one fetch stub (route table or
// raw impl), one response helper, one QueryClient wrapper, and the
// recorded call log. Test files import these instead of pasting a copy.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { createElement } from "react";
import type { ReactNode } from "react";
import { vi } from "vitest";

export type MinimalResponse = {
  status: number;
  text: () => Promise<string>;
  /** Present on SSE stubs; the plain JSON stubs leave it undefined. */
  headers?: { get: (name: string) => string | null };
  body?: ReadableStream<Uint8Array>;
};
export type Call = { method: string; url: string; body: string };
export type Route = { method: string; path: string; reply: MinimalResponse };

/** The fetch calls the current stub has seen; clearCalls() empties it. */
export const calls: Call[] = [];

/** One JSON response with an HTTP status (the common stub answer). */
export function respond(status: number, body: unknown): MinimalResponse {
  return { status, text: async () => JSON.stringify(body) };
}

/**
 * One SSE response whose frames arrive as separate stream chunks (the
 * streaming tests assert on the INCREMENTAL rendering between chunks).
 * Each entry is one full SSE frame, ending in its blank line.
 */
export function respondSSE(frames: string[], pauseMs = 10): MinimalResponse {
  const encoder = new TextEncoder();
  let index = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index >= frames.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(frames[index]));
      index += 1;
      // One macrotask per frame: the test can observe partial answers.
      return new Promise((resolve) => setTimeout(resolve, pauseMs));
    },
  });
  return {
    status: 200,
    headers: { get: (name: string) => (name === "content-type" ? "text/event-stream" : null) },
    text: async () => frames.join(""),
    body,
  };
}

/** Route every fetch through the route table; record each call. */
export function stubFetch(routes: Route[]): void {
  const impl = (
    url: string | URL,
    init?: { method?: string; body?: unknown },
  ): Promise<MinimalResponse> => {
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url: String(url), body: String(init?.body ?? "") });
    const hit = routes.find((r) => r.method === method && String(url).includes(r.path));
    if (hit) return Promise.resolve(hit.reply);
    return Promise.reject(new Error(`unexpected fetch: ${method} ${String(url)}`));
  };
  vi.stubGlobal("fetch", impl as unknown as typeof fetch);
}

/** Route every fetch through one raw implementation (no call log). */
export function stubFetchImpl(impl: unknown): void {
  vi.stubGlobal("fetch", impl as typeof fetch);
}

/** Empty the recorded call log (call it in afterEach). */
export function clearCalls(): void {
  calls.length = 0;
}

/**
 * A render wrapper with a fresh QueryClient per call. withRouter adds
 * the MemoryRouter page tests need; hook and widget tests pass false.
 */
export function makeWrapper(withRouter = true) {
  const client = new QueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(
      QueryClientProvider,
      { client },
      withRouter ? createElement(MemoryRouter, null, children) : children,
    );
  };
}
