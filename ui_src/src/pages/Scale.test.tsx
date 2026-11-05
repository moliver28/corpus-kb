import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { Call, MinimalResponse } from "../test/testUtils";
import calibrateOk from "../test/fixtures/calibrate-apply-ok-200.json";
import ingestStatusOk from "../test/fixtures/ingest-status-ok-200.json";
import ok200 from "../test/fixtures/ok-200.json";
import researchDemoOk from "../test/fixtures/research-demo-ok-200.json";
import unavailable503 from "../test/fixtures/unavailable-503.json";
import Scale from "./Scale";

/**
 * The Scale page from canonical fixtures: the self-polling run ledger
 * (runtime interval, hidden-tab pause, 10 s backoff after a 503 — all
 * under fake timers), the bulk folder walk, the research ingest with
 * its force warning, and the re-embed watch. The project filter rides
 * the hash query (restore, unknown-fallback, write rows).
 */

let query = "";

/** Reports the router location's query so rows can assert the write. */
function QueryProbe() {
  const search = useLocation().search;
  useEffect(() => {
    query = search;
  });
  return null;
}

/** A ledger with two projects, for the URL-synced filter rows. */
const twoProjectsLedger = {
  status: "success",
  ledger_present: true,
  runs: [
    {
      run_id: "run-one",
      status: "success",
      started_at: "2026-01-05T10:05:00Z",
      source_type: "upload",
      project_id: "proj-1",
    },
    {
      run_id: "run-two",
      status: "success",
      started_at: "2026-01-05T10:06:00Z",
      source_type: "upload",
      project_id: "proj-2",
    },
  ],
  incomplete_runs: 0,
  degraded: { chunks: 0, chunk_vectors: 0 },
};

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
  query = "";
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
      // One read on mount: the poll loop owns the ledger, and the
      // re-embed watch rides its answer (no second fetch).
      expect(ledgerCalls().length).toBe(1);
      expect(screen.getByText(strings.scaleLedgerPolling.replace("{sec}", "2"))).toBeTruthy();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(ledgerCalls().length).toBe(2);
      // Hidden: the loop parks on the visibilitychange wake — no fetches.
      setTabVisible(false);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });
      expect(ledgerCalls().length).toBe(2);
      expect(screen.getByText(strings.scaleLedgerPaused)).toBeTruthy();
      // The wake event polls at once, and the backoff state is clean.
      setTabVisible(true);
      await flush();
      expect(ledgerCalls().length).toBe(3);
      expect(screen.getByText(strings.scaleLedgerPolling.replace("{sec}", "2"))).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("backs off to ten seconds after a 503 and clears on a good poll", async () => {
    vi.useFakeTimers();
    try {
      setTabVisible(true);
      // The poll loop owns the ledger route: the first read answers 503,
      // everything after answers 200.
      let statusReads = 0;
      const seqImpl = (
        url: string | URL,
        init?: { method?: string; body?: unknown },
      ): Promise<MinimalResponse> => {
        const method = (init?.method ?? "GET").toUpperCase();
        calls.push({ method, url: String(url), body: String(init?.body ?? "") });
        if (String(url).includes("/api/ingest/status")) {
          statusReads += 1;
          const body = statusReads <= 1 ? unavailable503.response.body : ingestStatusOk.response.body;
          return Promise.resolve(respond(statusReads <= 1 ? 503 : 200, body));
        }
        return Promise.reject(new Error(`unexpected fetch: ${method} ${String(url)}`));
      };
      vi.stubGlobal("fetch", seqImpl as unknown as typeof fetch);
      render(<Scale />, { wrapper: makeWrapper() });
      await flush();
      expect(ledgerCalls().length).toBe(1);
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
      expect(ledgerCalls().length).toBe(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(8000);
      });
      expect(ledgerCalls().length).toBe(2);
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

  it("restores the project filter from the address query", async () => {
    setTabVisible(true);
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, twoProjectsLedger) },
    ]);
    render(<Scale />, { wrapper: makeWrapper(true, ["/scale?project=proj-2"]) });
    // The picker and the table both show the project words, so the wait
    // targets the combobox and the assert reads the table body only.
    // (The run id is the row key, never a cell.)
    await screen.findByRole("combobox");
    const tbody = document.querySelector(".ant-table-tbody");
    expect(tbody?.textContent).toContain("proj-2");
    expect(tbody?.textContent).not.toContain("proj-1");
  });

  it("reads an unknown project in the query as all runs", async () => {
    setTabVisible(true);
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, twoProjectsLedger) },
    ]);
    render(<Scale />, { wrapper: makeWrapper(true, ["/scale?project=ghost"]) });
    await screen.findByRole("combobox");
    const tbody = document.querySelector(".ant-table-tbody");
    expect(tbody?.textContent).toContain("proj-1");
    expect(tbody?.textContent).toContain("proj-2");
  });

  it("writes the project pick into the address query", async () => {
    setTabVisible(true);
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, twoProjectsLedger) },
    ]);
    render(
      <>
        <Scale />
        <QueryProbe />
      </>,
      { wrapper: makeWrapper() },
    );
    await screen.findByRole("combobox");
    // The dropdown's aria list mirrors the words of the table cells, so
    // the pick targets the visual .ant-select-item-option rows inside
    // the open dropdown. The router write rides a transition, so each
    // click flushes in an act.
    const pickFrom = (): Element[] => {
      const dropdown = document.querySelector(".ant-select-dropdown");
      return dropdown
        ? Array.from(dropdown.querySelectorAll(".ant-select-item-option"))
        : [];
    };
    fireEvent.mouseDown(screen.getByRole("combobox"));
    // The null value renders "All projects" in the selector too, so the
    // open wait matches both (the dropdown one only exists when open).
    await screen.findAllByText(strings.scaleRunsAll);
    const pick = pickFrom().find((el) => el.textContent === "proj-1");
    expect(pick).toBeTruthy();
    await act(async () => {
      fireEvent.click(pick!);
    });
    expect(query).toBe("?project=proj-1");
    fireEvent.mouseDown(screen.getByRole("combobox"));
    await screen.findAllByText(strings.scaleRunsAll);
    const all = pickFrom().find((el) => el.textContent === strings.scaleRunsAll);
    expect(all).toBeTruthy();
    await act(async () => {
      fireEvent.click(all!);
    });
    expect(query).toBe("");
  });

  it("shows the honest no-runs banner on an empty ledger", async () => {
    setTabVisible(true);
    stubFetch([
      {
        method: "GET",
        path: "/api/ingest/status",
        reply: respond(200, {
          status: "success",
          ledger_present: true,
          runs: [],
          incomplete_runs: 0,
          degraded: { chunks: 0, chunk_vectors: 0 },
        }),
      },
    ]);
    render(<Scale />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.scaleRunsNone)).toBeTruthy();
    // A fresh install gets the banner, not a bare JSON panel alone.
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("pages the ledger table instead of rendering every run at once", async () => {
    setTabVisible(true);
    const manyRuns = {
      status: "success",
      ledger_present: true,
      runs: Array.from({ length: 12 }, (_, index) => ({
        run_id: `run-${String(index + 1).padStart(2, "0")}`,
        status: "success",
        started_at: "2026-01-05T10:05:00Z",
        source_type: "upload",
        project_id: `proj-${String(index + 1).padStart(2, "0")}`,
      })),
      incomplete_runs: 0,
      degraded: { chunks: 0, chunk_vectors: 0 },
    };
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, manyRuns) },
    ]);
    render(<Scale />, { wrapper: makeWrapper() });
    await screen.findByRole("combobox");
    const bodyText = (): string =>
      document.querySelector(".ant-table-tbody")?.textContent ?? "";
    // Page one carries the first ten rows; the rest wait behind the pager.
    expect(bodyText()).toContain("proj-01");
    expect(bodyText()).not.toContain("proj-11");
    fireEvent.click(document.querySelector(".ant-pagination-item-2")!);
    expect(bodyText()).toContain("proj-11");
    expect(bodyText()).not.toContain("proj-01");
  });

  it("runs the research demo behind the gate and shows the narration", async () => {
    setTabVisible(true);
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, ingestStatusOk.response.body) },
      { method: "POST", path: "/api/research/demo", reply: respond(200, researchDemoOk.response.body) },
    ]);
    render(<Scale />, { wrapper: makeWrapper() });
    const demoCard = cardOf(strings.scaleDemoTitle);
    fireEvent.click(
      within(demoCard).getByRole("button", { name: strings.scaleDemoAction }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
    expect(await within(demoCard).findByText(strings.scaleDemoCompleted)).toBeTruthy();
    expect(within(demoCard).getByText(/Step 1: loaded the sample corpus/)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/research/demo"));
    expect(sent?.method).toBe("POST");
  });

  it("previews the calibration diff and gates the config write", async () => {
    setTabVisible(true);
    // The apply op answers the dry-run first, then the write run.
    const writeBody = {
      status: "success",
      result: {
        ...calibrateOk.response.body.result,
        mode: "write",
        applied_count: 1,
      },
    };
    let applyCalls = 0;
    const impl = (
      url: string | URL,
      init?: { method?: string; body?: unknown },
    ): Promise<MinimalResponse> => {
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ method, url: String(url), body: String(init?.body ?? "") });
      if (String(url).includes("/api/ingest/status")) {
        return Promise.resolve(respond(200, ingestStatusOk.response.body));
      }
      if (String(url).includes("/api/calibrate/apply")) {
        applyCalls += 1;
        return Promise.resolve(
          respond(200, applyCalls === 1 ? calibrateOk.response.body : writeBody),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${method} ${String(url)}`));
    };
    vi.stubGlobal("fetch", impl as unknown as typeof fetch);
    render(<Scale />, { wrapper: makeWrapper() });
    const calibrateCard = cardOf(strings.scaleCalibrateTitle);
    fireEvent.change(
      within(calibrateCard).getByPlaceholderText(strings.scaleCalibrateProfileLabel),
      { target: { value: "default" } },
    );
    fireEvent.click(
      within(calibrateCard).getByRole("button", { name: strings.scaleCalibratePreview }),
    );
    // The dry run shows the lock's diff and its winner, and writes nothing.
    expect(
      await within(calibrateCard).findByText(strings.scaleCalibrateChosen.replace("{chosen}", "bm25-tight")),
    ).toBeTruthy();
    expect(within(calibrateCard).getByText("retrieval.candidate_floor")).toBeTruthy();
    expect(within(calibrateCard).getByText("0.25")).toBeTruthy();
    // The write is a separate gated run; the confirm dialog names the count.
    fireEvent.click(
      within(calibrateCard).getByRole("button", { name: strings.scaleCalibrateApply }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Changed lines: 1/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
    expect(
      await within(calibrateCard).findByText(
        strings.scaleCalibrateWritten.replace("{count}", "1"),
      ),
    ).toBeTruthy();
    const write = calls.filter((c) => c.url.includes("/api/calibrate/apply"))[1];
    expect(write.body).toContain('"write":true');
  });

  it("never invents a changed-line count when the write answer drifts", async () => {
    setTabVisible(true);
    // The write run answers a status-only success: no result object, so
    // the count is unknown — the banner must say so, never "0".
    let applyCalls = 0;
    const impl = (
      url: string | URL,
      init?: { method?: string; body?: unknown },
    ): Promise<MinimalResponse> => {
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ method, url: String(url), body: String(init?.body ?? "") });
      if (String(url).includes("/api/ingest/status")) {
        return Promise.resolve(respond(200, ingestStatusOk.response.body));
      }
      if (String(url).includes("/api/calibrate/apply")) {
        applyCalls += 1;
        return Promise.resolve(
          respond(200, applyCalls === 1 ? calibrateOk.response.body : { status: "success" }),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${method} ${String(url)}`));
    };
    vi.stubGlobal("fetch", impl as unknown as typeof fetch);
    render(<Scale />, { wrapper: makeWrapper() });
    const calibrateCard = cardOf(strings.scaleCalibrateTitle);
    fireEvent.change(
      within(calibrateCard).getByPlaceholderText(strings.scaleCalibrateProfileLabel),
      { target: { value: "default" } },
    );
    fireEvent.click(
      within(calibrateCard).getByRole("button", { name: strings.scaleCalibratePreview }),
    );
    await within(calibrateCard).findByText("retrieval.candidate_floor");
    fireEvent.click(
      within(calibrateCard).getByRole("button", { name: strings.scaleCalibrateApply }),
    );
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
    expect(
      await within(calibrateCard).findByText(strings.scaleCalibrateWrittenUnknown),
    ).toBeTruthy();
    expect(within(calibrateCard).queryByText("Changed lines: 0")).toBeNull();
  });
});
