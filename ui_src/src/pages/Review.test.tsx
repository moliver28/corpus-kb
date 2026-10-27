import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { strings } from "../strings";
import assignmentsOk from "../test/fixtures/assignments-ok-200.json";
import ingestStatusOk from "../test/fixtures/ingest-status-ok-200.json";
import reembedOk from "../test/fixtures/reembed-ok-200.json";
import reviewExecuteOk from "../test/fixtures/review-execute-ok-200.json";
import topicsOk from "../test/fixtures/topics-ok-200.json";
import Review from "./Review";

/**
 * The Review page from canonical fixtures: the honest documents gap
 * banner (the read op has no manifest row yet), the degraded-chunks
 * banner with its re-embed summary, and the accept/override flow where
 * the override needs a note and the reviewer name is remembered.
 */

type MinimalResponse = { status: number; text: () => Promise<string> };
type Call = { method: string; url: string; body: string };

const calls: Call[] = [];

function respond(status: number, body: unknown): MinimalResponse {
  return { status, text: async () => JSON.stringify(body) };
}

function stubFetch(routes: { method: string; path: string; reply: MinimalResponse }[]): void {
  const impl = (url: string | URL, init?: { method?: string; body?: unknown }): Promise<MinimalResponse> => {
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url: String(url), body: String(init?.body ?? "") });
    const hit = routes.find((r) => r.method === method && String(url).includes(r.path));
    if (hit) return Promise.resolve(hit.reply);
    return Promise.reject(new Error(`unexpected fetch: ${method} ${String(url)}`));
  };
  vi.stubGlobal("fetch", impl as unknown as typeof fetch);
}

function makeWrapper() {
  const client = new QueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  calls.length = 0;
});

function reviewRoutes(): { method: string; path: string; reply: MinimalResponse }[] {
  return [
    { method: "GET", path: "/api/ingest/status", reply: respond(200, ingestStatusOk.response.body) },
    { method: "GET", path: "/api/coding/assignments", reply: respond(200, assignmentsOk.response.body) },
    { method: "GET", path: "/api/topics", reply: respond(200, topicsOk.response.body) },
  ];
}

describe("Review page", () => {
  it("shows the honest gap where the documents read has no manifest row", async () => {
    stubFetch(reviewRoutes());
    render(<Review />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.reviewDocsNotWired)).toBeTruthy();
    // The degraded read answers through the same mount cycle.
    expect(await screen.findByText(/lack usable embeddings/)).toBeTruthy();
  });

  it("renders the degraded counts and the re-embed summary", async () => {
    stubFetch([
      ...reviewRoutes(),
      { method: "POST", path: "/api/ingest/reembed", reply: respond(200, reembedOk.response.body) },
    ]);
    render(<Review />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(
        strings.reviewDegradedCounts.replace("{chunks}", "3").replace("{vectors}", "3"),
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: strings.reviewDegradedAction }));
    expect(
      await screen.findByText(
        strings.reviewDegradedDone
          .replace("{scanned}", "3")
          .replace("{fixed}", "3")
          .replace("{failed}", "0"),
      ),
    ).toBeTruthy();
  });

  it("records an accept decision with the remembered reviewer", async () => {
    window.localStorage.setItem("corpus-kb:reviewer", "Alex");
    stubFetch([
      ...reviewRoutes(),
      {
        method: "POST",
        path: "/api/review/execute",
        reply: respond(200, reviewExecuteOk.response.body),
      },
    ]);
    render(<Review />, { wrapper: makeWrapper() });
    // Two fixture rows; the first row's Accept runs the decision flow.
    const acceptButtons = await screen.findAllByRole("button", { name: strings.reviewAccept });
    fireEvent.click(acceptButtons[0]);
    expect(await screen.findByText(strings.reviewDecisionDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/review/execute"));
    expect(sent).toBeTruthy();
    expect(sent?.body).toContain('"decision":"accept"');
    expect(sent?.body).toContain('"reviewer":"Alex"');
    // The server reviews the aggregate UUID, never the int row id.
    expect(sent?.body).toContain(
      '"assignment_id":"3f6d2c9a-1111-4c4c-8d8d-222222222222"',
    );
  });

  it("blocks an override until the note is written", async () => {
    window.localStorage.setItem("corpus-kb:reviewer", "Alex");
    stubFetch([
      ...reviewRoutes(),
      {
        method: "POST",
        path: "/api/review/execute",
        reply: respond(200, reviewExecuteOk.response.body),
      },
    ]);
    render(<Review />, { wrapper: makeWrapper() });
    const overrideButtons = await screen.findAllByRole("button", { name: strings.reviewOverride });
    fireEvent.click(overrideButtons[0]);
    // The inline override form opens under the queue; the empty note
    // keeps the gate shut with the honest warning.
    fireEvent.click(
      await screen.findByRole("button", { name: strings.reviewOverrideSubmit }),
    );
    expect(await screen.findByText(strings.reviewNoteRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/review/execute"))).toBe(false);
    fireEvent.change(screen.getByPlaceholderText(strings.reviewNoteLabel), {
      target: { value: "Wrong code; the unit is about pricing." },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.reviewOverrideSubmit }));
    expect(await screen.findByText(strings.reviewDecisionDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/review/execute"));
    expect(sent?.body).toContain('"decision":"override"');
    expect(sent?.body).toContain("pricing");
    expect(sent?.body).toContain(
      '"assignment_id":"3f6d2c9a-1111-4c4c-8d8d-222222222222"',
    );
  });

  it("asks for a reviewer name before any decision", async () => {
    stubFetch(reviewRoutes());
    render(<Review />, { wrapper: makeWrapper() });
    const acceptButtons = await screen.findAllByRole("button", { name: strings.reviewAccept });
    fireEvent.click(acceptButtons[0]);
    expect(await screen.findByText(strings.reviewReviewerRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/review/execute"))).toBe(false);
  });

  it("renders the queue rows with plain state words and the topics table", async () => {
    stubFetch(reviewRoutes());
    render(<Review />, { wrapper: makeWrapper() });
    expect(await screen.findByText("energy_policy")).toBeTruthy();
    expect(screen.getAllByText(strings.reviewStateNeedsReview).length).toBeGreaterThan(0);
    expect(screen.getAllByText(strings.reviewStateAuto).length).toBeGreaterThan(0);
    expect(screen.queryByText("review")).toBeNull();
    expect(await screen.findByText("Renewable energy policy")).toBeTruthy();
    expect(screen.getByText("62%")).toBeTruthy();
  });
});
