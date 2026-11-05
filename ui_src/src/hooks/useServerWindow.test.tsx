import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { MinimalResponse } from "../test/testUtils";
import { useServerWindow, windowPagerTotal } from "./useServerWindow";
import type { ServerWindow } from "./useServerWindow";

/**
 * The one server-window seam: the limit+1 first fetch, the has-more
 * flag, the pager's window changes, the empty-window step-back after a
 * delete, and refresh. The generic hook is probed with one small row
 * shape and its guard, the same way the documents table consumes it.
 */

interface ProbeRow {
  id: string;
}

interface ProbeEnvelope {
  status: string;
  result: ProbeRow[];
}

function isProbeEnvelope(value: unknown): value is ProbeEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).result;
  return (
    Array.isArray(rows) &&
    rows.every(
      (row) =>
        typeof row === "object" &&
        row !== null &&
        typeof (row as Record<string, unknown>).id === "string",
    )
  );
}

const WINDOW = 3;

// The latest settled seam state (waitFor reads it after render ticks).
let latest: ServerWindow<ProbeRow> | null = null;

/** Renders the hook; the buttons drive its controls by hand. */
function Harness() {
  const win = useServerWindow<ProbeRow, ProbeEnvelope>(
    "documents-list",
    WINDOW,
    isProbeEnvelope,
  );
  // Report outside render (the purity rule): the probe reads settled
  // state after effects run, the same channel useQueryParam tests use.
  useEffect(() => {
    latest = win;
  });
  return (
    <div>
      <button onClick={() => win.goto(2)}>next</button>
      <button onClick={() => win.refresh()}>reload</button>
      <button onClick={() => win.stepBackAfterDelete(1)}>remove only row</button>
      <button onClick={() => win.stepBackAfterDelete(2)}>remove one of two</button>
      <ul>
        {win.rows.map((row) => (
          <li key={row.id}>{row.id}</li>
        ))}
      </ul>
      <p>{win.hasMore ? "more" : "last"}</p>
    </div>
  );
}

function renderProbe(): void {
  latest = null;
  render(<Harness />, { wrapper: makeWrapper() });
}

function rowsReply(count: number, firstOffset = 0): MinimalResponse {
  const rows = Array.from({ length: count }, (_, i) => ({
    id: `row-${String(firstOffset + i).padStart(2, "0")}`,
  }));
  return respond(200, { status: "success", result: rows });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
  latest = null;
});

describe("useServerWindow", () => {
  it("fetches one row over the window size and reports has-more", async () => {
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(WINDOW + 1) }]);
    renderProbe();
    await waitFor(() => expect(latest?.rows.length).toBe(WINDOW));
    expect(calls.some((c) => c.url.includes("/api/documents?limit=4&offset=0"))).toBe(
      true,
    );
    expect(latest?.hasMore).toBe(true);
    expect(screen.getByText("more")).toBeTruthy();
  });

  it("reports the last window when the fetch holds only the window's rows", async () => {
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(WINDOW) }]);
    renderProbe();
    await waitFor(() => expect(latest?.rows.length).toBe(WINDOW));
    expect(latest?.hasMore).toBe(false);
    expect(screen.getByText("last")).toBeTruthy();
  });

  it("reads the next window through the pager offset", async () => {
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(WINDOW + 1) }]);
    renderProbe();
    await waitFor(() => expect(latest?.rows.length).toBe(WINDOW));
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(2, 3) }]);
    fireEvent.click(screen.getByRole("button", { name: "next" }));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("offset=3"))).toBe(true),
    );
  });

  it("steps back one window when a delete empties the window", async () => {
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(WINDOW) }]);
    renderProbe();
    await waitFor(() => expect(latest?.rows.length).toBe(WINDOW));
    fireEvent.click(screen.getByRole("button", { name: "next" }));
    await waitFor(() => expect(latest?.page).toBe(2));
    clearCalls();
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(1) }]);
    fireEvent.click(screen.getByRole("button", { name: "remove only row" }));
    await waitFor(() => expect(latest?.page).toBe(1));
    // Exactly one fetch: the page-state effect owns the step-back read.
    expect(calls.filter((c) => c.url.includes("/api/documents?")).length).toBe(1);
    expect(calls[0]?.url.includes("offset=0")).toBe(true);
  });

  it("rereads the same window when the deleted row was not the only one", async () => {
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(WINDOW) }]);
    renderProbe();
    await waitFor(() => expect(latest?.rows.length).toBe(WINDOW));
    clearCalls();
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(2) }]);
    fireEvent.click(screen.getByRole("button", { name: "remove one of two" }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(latest?.page).toBe(1);
    expect(calls.every((c) => c.url.includes("offset=0"))).toBe(true);
  });

  it("falls to a same-window refresh when a lone row is deleted on page 1", async () => {
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(WINDOW) }]);
    renderProbe();
    await waitFor(() => expect(latest?.rows.length).toBe(WINDOW));
    clearCalls();
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(1) }]);
    fireEvent.click(screen.getByRole("button", { name: "remove only row" }));
    await waitFor(() => expect(calls.length).toBe(1));
    expect(latest?.page).toBe(1);
    expect(calls.every((c) => c.url.includes("offset=0"))).toBe(true);
  });

  it("rereads the current window on a direct refresh call", async () => {
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(WINDOW + 1) }]);
    renderProbe();
    await waitFor(() => expect(latest?.rows.length).toBe(WINDOW));
    fireEvent.click(screen.getByRole("button", { name: "next" }));
    await waitFor(() => expect(latest?.page).toBe(2));
    clearCalls();
    stubFetch([{ method: "GET", path: "/api/documents", reply: rowsReply(WINDOW) }]);
    fireEvent.click(screen.getByRole("button", { name: "reload" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("offset=3"))).toBe(true));
    expect(latest?.page).toBe(2);
    await waitFor(() => expect(latest?.rows.length).toBe(WINDOW));
  });

  it("windowPagerTotal adds the sentinel row only when a next window exists", () => {
    expect(windowPagerTotal(1, 50, 50, true)).toBe(51);
    expect(windowPagerTotal(1, 50, 50, false)).toBe(50);
    expect(windowPagerTotal(2, 50, 20, false)).toBe(70);
  });
});
