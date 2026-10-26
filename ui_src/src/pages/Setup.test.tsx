import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { strings } from "../strings";
import demoLoaded from "../test/fixtures/demo-loaded-200.json";
import demoPartial from "../test/fixtures/demo-partial-200.json";
import doctorOk from "../test/fixtures/doctor-ok.json";
import Setup from "./Setup";

/**
 * The Setup page from canonical fixtures (src/test/fixtures — the same
 * recorded files the drift gate guards): the doctor card renders the
 * honest model-server warning with the server's own words, the
 * demo-load flow goes confirm -> long op -> receipt, and the upload cap
 * line shows before any upload happens.
 */

type MinimalResponse = { status: number; text: () => Promise<string> };

function respond(status: number, body: unknown): MinimalResponse {
  return { status, text: async () => JSON.stringify(body) };
}

function stubFetch(routes: { method: string; path: string; reply: MinimalResponse }[]): void {
  const impl = (url: string | URL | Request, init?: { method?: string }): Promise<MinimalResponse> => {
    const method = (init?.method ?? "GET").toUpperCase();
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
});

describe("Setup page", () => {
  it("renders the doctor table and an honest model-server warning", async () => {
    stubFetch([{ method: "GET", path: "/api/doctor", reply: respond(200, doctorOk.response.body) }]);
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

  it("runs the demo load behind the confirm gate and shows the receipt", async () => {
    stubFetch([
      { method: "GET", path: "/api/doctor", reply: respond(200, doctorOk.response.body) },
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
      { method: "GET", path: "/api/doctor", reply: respond(200, doctorOk.response.body) },
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
      { method: "GET", path: "/api/doctor", reply: respond(200, doctorOk.response.body) },
      { method: "POST", path: "/api/demo/load", reply: respond(200, demoPartial.response.body) },
    ]);
    render(<Setup />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.setupDemoAction }));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    expect(await screen.findByText(strings.receiptPartial)).toBeTruthy();
    expect(screen.queryByRole("button", { name: strings.setupDemoNext })).toBeNull();
  });

  it("shows the upload cap and the upload action before any upload", async () => {
    stubFetch([{ method: "GET", path: "/api/doctor", reply: respond(200, doctorOk.response.body) }]);
    render(<Setup />, { wrapper: makeWrapper() });
    expect(await screen.findByText("Each file can be up to 50 MB.")).toBeTruthy();
    expect(screen.getByRole("button", { name: strings.setupUploadAction })).toBeTruthy();
  });
});
