import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { Call, MinimalResponse } from "../test/testUtils";
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

function ledgerCalls(): Call[] {
  return calls.filter((c) => c.method === "GET" && c.url.includes("/api/ingest/status"));
}

function cardOf(title: string): HTMLElement {
  const card = screen
    .getAllByText(title)
    .map((el) => el.closest(".ant-card"))
    .find((c) => c !== null && c.querySelector(".ant-card-head-title")?.textContent === title);
  if (card === undefined) {
    throw new Error(`no card found for ${title}`);
  }
  return card as HTMLElement;
}

// jsdom keeps document.hidden on the Document prototype; a getter spy
// fakes tab visibility, and the event wakes the parked poll loop.
function setTabVisible(visible: boolean): void {
  vi.spyOn(document, "hidden", "get").mockReturnValue(!visible);
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
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
  clearCalls();
});

describe("Scale page", () => {
  it("polls the ledger on the runtime interval and pauses when hidden", async () => {
    vi.useFakeTimers();
    try {
      setTabVisible(true);
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
      // Hidden: the loop parks on the visibilitychange wake — no fetches.
      setTabVisible(false);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });
      expect(ledgerCalls().length).toBe(3);
      expect(screen.getByText(strings.scaleLedgerPaused)).toBeTruthy();
      // The wake event polls at once, and the backoff state is clean.
      setTabVisible(true);
      await flush();
      expect(ledgerCalls().length).toBe(4);
      expect(screen.getByText(strings.scaleLedgerPolling.replace("{sec}", "2"))).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("backs off to ten seconds after a 503 and clears on a good poll", async () => {
    vi.useFakeTimers();
    try {
      setTabVisible(true);
      // The poll and the re-embed watch share the route: the first two
      // status reads answer 503, everything after answers 200.
      let statusReads = 0;
      const seqImpl = (
        url: string | URL,
        init?: { method?: string; body?: unknown },
      ): Promise<MinimalResponse> => {
        const method = (init?.method ?? "GET").toUpperCase();
        calls.push({ method, url: String(url), body: String(init?.body ?? "") });
        if (String(url).includes("/api/ingest/status")) {
          statusReads += 1;
          const body = statusReads <= 2 ? unavailable503.response.body : ingestStatusOk.response.body;
          return Promise.resolve(respond(statusReads <= 2 ? 503 : 200, body));
        }
        return Promise.reject(new Error(`unexpected fetch: ${method} ${String(url)}`));
      };
      vi.stubGlobal("fetch", seqImpl as unknown as typeof fetch);
      render(<Scale />, { wrapper: makeWrapper() });
      await flush();
      expect(ledgerCalls().length).toBe(2);
      expect(screen.getByText(strings.scaleLedgerBackoff)).toBeTruthy();
      // Spec F6: the server's own message shows beneath the backoff.
      // (The re-embed watch's unavailable panel repeats it — fine.)
      expect(
        screen.getAllByText("The query handler is not ported to the DuckDB backend.").length,
      ).toBeGreaterThan(0);
      // The normal interval passes with no poll; the backoff fires it.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(ledgerCalls().length).toBe(2);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(8000);
      });
      expect(ledgerCalls().length).toBe(3);
      // The good poll clears the backoff state.
      expect(screen.queryByText(strings.scaleLedgerBackoff)).toBeNull();
      expect(screen.getByText(strings.scaleLedgerPolling.replace("{sec}", "2"))).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("runs the bulk folder walk and the research ingest with the force warning", async () => {
    setTabVisible(true);
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
    // Force re-ingest is a gated run (spec F10); the dialog repeats the
    // warning, then the long op runs with its honest wait.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(strings.scaleForceWarning)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
    expect(await within(researchCard).findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/research/ingest"));
    expect(sent?.body).toContain('"path":"/data/corpus/**"');
    expect(sent?.body).toContain('"force":true');
  });
});
