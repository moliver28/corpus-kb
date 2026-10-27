import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { strings } from "../strings";
import ingestStatusOk from "../test/fixtures/ingest-status-ok-200.json";
import ok200 from "../test/fixtures/ok-200.json";
import unavailable503 from "../test/fixtures/unavailable-503.json";
import Scale from "./Scale";

/**
 * The Scale page from canonical fixtures: the self-polling run ledger
 * (runtime interval, hidden-tab pause, 10 s backoff after a 503 — all
 * under fake timers), the bulk folder walk, the research ingest with
 * its force warning, and the re-embed watch.
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

function ledgerCalls(): Call[] {
  return calls.filter((c) => c.method === "GET" && c.url.includes("/api/ingest/status"));
}

function cardOf(title: string): HTMLElement {
  const card = screen.getByText(title).closest(".ant-card");
  if (card === null) {
    throw new Error(`no card found for ${title}`);
  }
  return card as HTMLElement;
}

// jsdom keeps document.hidden on the Document prototype; a getter spy
// fakes tab visibility per test.
function setHidden(hidden: boolean): void {
  vi.spyOn(document, "hidden", "get").mockReturnValue(hidden);
}

async function flush(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  calls.length = 0;
});

describe("Scale page", () => {
  it("polls the ledger on the runtime interval and pauses when hidden", async () => {
    vi.useFakeTimers();
    try {
      setHidden(false);
      stubFetch([
        {
          method: "GET",
          path: "/api/ingest/status",
          reply: respond(200, ingestStatusOk.response.body),
        },
      ]);
      render(<Scale />, { wrapper: makeWrapper() });
      await flush();
      // Two reads: the poll loop's first poll plus the re-embed watch's
      // own mount read of the same ledger.
      expect(ledgerCalls().length).toBe(2);
      expect(screen.getByText(strings.scaleLedgerPolling.replace("{sec}", "2"))).toBeTruthy();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(ledgerCalls().length).toBe(3);
      // Hidden: the loop idles without a single fetch.
      setHidden(true);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });
      expect(ledgerCalls().length).toBe(3);
      expect(screen.getByText(strings.scaleLedgerPaused)).toBeTruthy();
      // Visible again: the next iteration polls at once.
      setHidden(false);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(ledgerCalls().length).toBe(4);
      expect(screen.getByText(strings.scaleLedgerPolling.replace("{sec}", "2"))).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("backs off to ten seconds after a 503", async () => {
    vi.useFakeTimers();
    try {
      setHidden(false);
      stubFetch([
        {
          method: "GET",
          path: "/api/ingest/status",
          reply: respond(503, unavailable503.response.body),
        },
      ]);
      render(<Scale />, { wrapper: makeWrapper() });
      await flush();
      // Poll + the re-embed watch's mount read, both 503.
      expect(ledgerCalls().length).toBe(2);
      expect(screen.getByText(strings.scaleLedgerBackoff)).toBeTruthy();
      // The normal interval passes with no poll; the backoff fires it.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(ledgerCalls().length).toBe(2);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(8000);
      });
      expect(ledgerCalls().length).toBe(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("runs the bulk folder walk and the research ingest with the force warning", async () => {
    setHidden(false);
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, ingestStatusOk.response.body) },
      { method: "POST", path: "/api/research/ingest", reply: respond(200, ok200.response.body) },
    ]);
    render(<Scale />, { wrapper: makeWrapper() });
    // The re-embed watch rides the same ledger read (degraded counts).
    expect(
      await screen.findByText(
        strings.reviewDegradedCounts.replace("{chunks}", "3").replace("{vectors}", "3"),
      ),
    ).toBeTruthy();
    expect(screen.getByText(strings.scaleBulkTitle)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(strings.scaleResearchPathLabel), {
      target: { value: "/data/corpus/**" },
    });
    const researchCard = cardOf(strings.scaleResearchTitle);
    fireEvent.click(
      within(researchCard).getByRole("button", { name: strings.scaleForceToggle }),
    );
    expect(screen.getByText(strings.scaleForceWarning)).toBeTruthy();
    fireEvent.click(
      within(researchCard).getByRole("button", { name: strings.runnerRunLabel }),
    );
    expect(await within(researchCard).findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/research/ingest"));
    expect(sent?.body).toContain('"path":"/data/corpus/**"');
    expect(sent?.body).toContain('"force":true');
  });
});
