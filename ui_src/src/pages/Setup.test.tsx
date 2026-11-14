import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { Route } from "../test/testUtils";
import demoDegraded from "../test/fixtures/demo-degraded-200.json";
import demoLoaded from "../test/fixtures/demo-loaded-200.json";
import demoPartial from "../test/fixtures/demo-partial-200.json";
import documentStatsOk from "../test/fixtures/document-stats-ok-200.json";
import doctorOk from "../test/fixtures/doctor-ok.json";
import statsOk from "../test/fixtures/stats-ok-200.json";
import Setup from "./Setup";

/**
 * The Setup page from canonical fixtures (src/test/fixtures — the same
 * recorded files the drift gate guards): the doctor card renders the
 * honest model-server warning with the server's own words, the
 * demo-load flow goes confirm -> long op -> receipt, and the upload cap
 * line shows before any upload happens.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** The Wave 3 stats strip reads (on mount with the page). */
function statsRoutes(): Route[] {
  return [
    { method: "GET", path: "/api/stats", reply: respond(200, statsOk.response.body) },
    { method: "GET", path: "/api/document-stats", reply: respond(200, documentStatsOk.response.body) },
  ];
}

/** The doctor read plus the stats strip reads. */
function setupRoutes(): Route[] {
  return [
    { method: "GET", path: "/api/doctor", reply: respond(200, doctorOk.response.body) },
    ...statsRoutes(),
  ];
}

describe("Setup page", () => {
  it("renders the doctor table and an honest model-server warning", async () => {
    stubFetch(setupRoutes());
    render(<Setup />, { wrapper: makeWrapper() });
    // The banner joins the fixed words with the server message, and both
    // the banner div and its content span match — so findAllByText.
    expect(
      (await screen.findAllByText(/model server is not reachable/)).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(/connection refused/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/postgres is up/).length).toBeGreaterThan(0);
    expect(screen.getByText("database backend")).toBeTruthy();
    // Probe enum words never render raw; "ok" shows its plain word.
    expect(screen.getAllByText(strings.setupStateOk).length).toBeGreaterThan(0);
    expect(screen.queryByText("ok")).toBeNull();
  });

  it("shows the embedding dims and the reranker state as plain lines", async () => {
    stubFetch(setupRoutes());
    render(<Setup />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(
        strings.setupEmbeddingLine
          .replace("{model}", "nomic-embed-text")
          .replace("{dims}", "768"),
      ),
    ).toBeTruthy();
    expect(screen.getByText(strings.setupRerankerOff)).toBeTruthy();
  });

  it("warns when the embedding width is known and under 1024", async () => {
    stubFetch(setupRoutes());
    render(<Setup />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.setupEmbeddingDimsLow)).toBeTruthy();
  });

  it("does not warn when the embedding width is unknown", async () => {
    const body = JSON.parse(JSON.stringify(doctorOk.response.body));
    body.result.embedding.dimensions = null;
    stubFetch([
      ...statsRoutes(),
      { method: "GET", path: "/api/doctor", reply: respond(200, body) },
    ]);
    render(<Setup />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(
        strings.setupEmbeddingLine
          .replace("{model}", "nomic-embed-text")
          .replace("{dims}", strings.setupEmbeddingDimsUnknown),
      ),
    ).toBeTruthy();
    // Honest-degraded rule: an unknown width never renders a fake warning.
    expect(screen.queryByText(strings.setupEmbeddingDimsLow)).toBeNull();
  });

  it("does not warn when the embedding width reaches the research width", async () => {
    const body = JSON.parse(JSON.stringify(doctorOk.response.body));
    body.result.embedding.dimensions = 2560;
    stubFetch([
      ...statsRoutes(),
      { method: "GET", path: "/api/doctor", reply: respond(200, body) },
    ]);
    render(<Setup />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(
        strings.setupEmbeddingLine
          .replace("{model}", "nomic-embed-text")
          .replace("{dims}", "2560"),
      ),
    ).toBeTruthy();
    expect(screen.queryByText(strings.setupEmbeddingDimsLow)).toBeNull();
  });

  it("hides the embedding and reranker lines on an older payload", async () => {
    const body = JSON.parse(JSON.stringify(doctorOk.response.body));
    delete body.result.embedding;
    delete body.result.reranker;
    stubFetch([
      ...statsRoutes(),
      { method: "GET", path: "/api/doctor", reply: respond(200, body) },
    ]);
    render(<Setup />, { wrapper: makeWrapper() });
    expect(
      (await screen.findAllByText(/model server is not reachable/)).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/Embedding:/)).toBeNull();
    expect(screen.queryByText(/Reranker:/)).toBeNull();
  });

  it("runs the demo load behind the confirm gate and shows the receipt", async () => {
    stubFetch([
      ...setupRoutes(),
      { method: "POST", path: "/api/demo/load", reply: respond(200, demoLoaded.response.body) },
    ]);
    render(<Setup />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.setupDemoAction }));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    expect(screen.getByRole("button", { name: strings.setupDemoNext })).toBeTruthy();
  });

  it("routes a status-less envelope to the unreadable panel, never Done", async () => {
    stubFetch([
      ...setupRoutes(),
      { method: "POST", path: "/api/demo/load", reply: respond(200, {}) },
    ]);
    render(<Setup />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.setupDemoAction }));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    expect(await screen.findByText(strings.unreadableResponse)).toBeTruthy();
    expect(screen.queryByText(strings.receiptDone)).toBeNull();
  });

  it("keeps the viewer on a partial receipt with no next-stage jump", async () => {
    stubFetch([
      ...setupRoutes(),
      { method: "POST", path: "/api/demo/load", reply: respond(200, demoPartial.response.body) },
    ]);
    render(<Setup />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.setupDemoAction }));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    expect(await screen.findByText(strings.receiptPartial)).toBeTruthy();
    expect(screen.queryByRole("button", { name: strings.setupDemoNext })).toBeNull();
  });

  it("shows the degraded note when a loaded file carries no vectors", async () => {
    stubFetch([
      { method: "GET", path: "/api/doctor", reply: respond(200, doctorOk.response.body) },
      { method: "POST", path: "/api/demo/load", reply: respond(200, demoDegraded.response.body) },
    ]);
    render(<Setup />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.setupDemoAction }));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    // The success receipt never reads clean: the degraded note sits
    // above the receipt headline.
    expect(await screen.findByText(strings.setupDemoDegraded)).toBeTruthy();
    expect(screen.getByText(strings.receiptDone)).toBeTruthy();
  });

  it("keeps the ingest surfaces off the stage (they live on Ingest)", async () => {
    stubFetch(setupRoutes());
    render(<Setup />, { wrapper: makeWrapper() });
    expect(await screen.findByRole("button", { name: strings.setupDemoAction })).toBeTruthy();
    expect(
      screen.queryByText(strings.setupUploadCap.replace("{mb}", "50")),
    ).toBeNull();
  });
});
