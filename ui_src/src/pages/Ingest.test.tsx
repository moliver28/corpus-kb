import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { strings } from "../strings";
import demoLoaded from "../test/fixtures/demo-loaded-200.json";
import ok200 from "../test/fixtures/ok-200.json";
import Ingest from "./Ingest";

/**
 * The Ingest page from canonical fixtures: one tab per source, each a
 * typed form over its single manifest op. The upload cap moved here
 * from Setup; the paste tab shows the honest gap banner; the sample
 * tab reuses the demo-load card Setup keeps as its quick start.
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

function openTab(label: string): void {
  fireEvent.click(screen.getByRole("button", { name: label }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  calls.length = 0;
});

describe("Ingest page", () => {
  it("lands on the Files tab with the upload cap and no Setup duplication", async () => {
    stubFetch([]);
    render(<Ingest />, { wrapper: makeWrapper() });
    expect(await screen.findByText("Each file can be up to 50 MB.")).toBeTruthy();
    expect(screen.getByRole("button", { name: strings.setupUploadAction })).toBeTruthy();
  });

  it("ingests one web address, with the crawl toggle in the request", async () => {
    stubFetch([{ method: "POST", path: "/api/ingest/url", reply: respond(200, ok200.response.body) }]);
    render(<Ingest />, { wrapper: makeWrapper() });
    openTab(strings.ingestTabWebsite);
    fireEvent.click(await screen.findByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.ingestUrlRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/ingest/url"))).toBe(false);
    fireEvent.change(screen.getByPlaceholderText(strings.ingestUrlLabel), {
      target: { value: "https://example.com/notes" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.ingestCrawlToggle }));
    expect(
      screen.getByRole("button", { name: strings.ingestCrawlToggle }).getAttribute("aria-pressed"),
    ).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/ingest/url"));
    expect(sent?.body).toContain('"url":"https://example.com/notes"');
    expect(sent?.body).toContain('"crawl":true');
  });

  it("shows the honest gap on the paste-text tab", async () => {
    stubFetch([]);
    render(<Ingest />, { wrapper: makeWrapper() });
    openTab(strings.ingestTabPaste);
    expect(await screen.findByText(strings.ingestPasteNotWired)).toBeTruthy();
  });

  it("ingests a server folder with subfolders on by default", async () => {
    stubFetch([
      { method: "POST", path: "/api/ingest/directory", reply: respond(200, ok200.response.body) },
    ]);
    render(<Ingest />, { wrapper: makeWrapper() });
    openTab(strings.ingestTabServerPath);
    fireEvent.click(await screen.findByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.ingestDirRequired)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(strings.ingestDirLabel), {
      target: { value: "/data/corpus" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/ingest/directory"));
    expect(sent?.body).toContain('"directory_path":"/data/corpus"');
    expect(sent?.body).toContain('"recursive":true');
  });

  it("ingests one transcript from its server path", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/research/ingest-transcript",
        reply: respond(200, ok200.response.body),
      },
    ]);
    render(<Ingest />, { wrapper: makeWrapper() });
    openTab(strings.ingestTabTranscripts);
    fireEvent.click(await screen.findByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.ingestTranscriptPathRequired)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(strings.ingestTranscriptPathLabel), {
      target: { value: "/data/interview-01.docx" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/research/ingest-transcript"));
    expect(sent?.body).toContain('"file_path":"/data/interview-01.docx"');
  });

  it("loads the sample knowledge behind its confirm gate", async () => {
    stubFetch([
      { method: "POST", path: "/api/demo/load", reply: respond(200, demoLoaded.response.body) },
    ]);
    render(<Ingest />, { wrapper: makeWrapper() });
    openTab(strings.ingestTabSample);
    fireEvent.click(await screen.findByRole("button", { name: strings.setupDemoAction }));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    expect(screen.getByRole("button", { name: strings.setupDemoNext })).toBeTruthy();
  });
});
