import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { makeWrapper, respond, stubFetch } from "../test/testUtils";
import documentStatsOk from "../test/fixtures/document-stats-ok-200.json";
import statsOk from "../test/fixtures/stats-ok-200.json";
import StatsStrip from "./StatsStrip";

/**
 * The corpus stats strip from canonical fixtures: one badge row per
 * corpus count (the handler's own key order) plus one badge row per
 * source type. Counts render in the mono voice; a failed read keeps
 * its honest state instead of a fake zero.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StatsStrip", () => {
  it("renders the corpus counts and the per-source-type rows", async () => {
    stubFetch([
      { method: "GET", path: "/api/stats", reply: respond(200, statsOk.response.body) },
      { method: "GET", path: "/api/document-stats", reply: respond(200, documentStatsOk.response.body) },
    ]);
    render(<StatsStrip />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.statsWordDocuments)).toBeTruthy();
    expect(screen.getByText(strings.statsWordChunks)).toBeTruthy();
    // Chunks and vectors both read 42 in the fixture.
    expect(screen.getAllByText("42").length).toBe(2);
    expect(screen.getByText("310")).toBeTruthy();
    // The per-source-type rows carry the server's own type word.
    expect(screen.getByText("pdf")).toBeTruthy();
    expect(screen.getByText("text")).toBeTruthy();
  });

  it("never shows a fake zero when a read fails", async () => {
    stubFetch([
      { method: "GET", path: "/api/stats", reply: respond(500, { status: "error" }) },
      { method: "GET", path: "/api/document-stats", reply: respond(200, documentStatsOk.response.body) },
    ]);
    render(<StatsStrip />, { wrapper: makeWrapper() });
    expect(await screen.findByText("pdf")).toBeTruthy();
    expect(screen.queryByText(strings.statsWordDocuments)).toBeNull();
    expect(screen.queryByText("310")).toBeNull();
  });
});
