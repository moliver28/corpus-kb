import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import demoLoaded from "../test/fixtures/demo-loaded-200.json";
import ingestDirectoryOk from "../test/fixtures/ingest-directory-ok-200.json";
import ingestUrlOk from "../test/fixtures/ingest-url-ok-200.json";
import ok200 from "../test/fixtures/ok-200.json";
import queueStatusOk from "../test/fixtures/queue-status-ok-200.json";
import researchIngestOk from "../test/fixtures/research-ingest-ok-200.json";
import Ingest from "./Ingest";

/**
 * The Ingest page from canonical fixtures: one tab per source, each a
 * typed form over its single manifest op. The upload cap moved here
 * from Setup; the paste tab shows the honest gap banner; the sample
 * tab reuses the demo-load card Setup keeps as its quick start. The
 * active tab rides the hash query (default, restore, write rows).
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

function openTab(label: string): void {
  fireEvent.click(screen.getByRole("button", { name: label }));
}

function cardOf(title: string): HTMLElement {
  // A switcher button can share the card title's words; the real card
  // head title is the match inside .ant-card-head-title.
  const card = screen
    .getAllByText(title)
    .map((el) => el.closest(".ant-card"))
    .find((c) => c !== null && c.querySelector(".ant-card-head-title")?.textContent === title);
  if (card === undefined) {
    throw new Error(`no card found for ${title}`);
  }
  return card as HTMLElement;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
  query = "";
});

describe("Ingest page", () => {
  it("lands on the Files tab with the upload cap and no Setup duplication", async () => {
    stubFetch([]);
    render(<Ingest />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(strings.setupUploadCap.replace("{mb}", "50")),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: strings.setupUploadAction })).toBeTruthy();
  });

  it("answers an empty upload submit with the hint, never a dead button", async () => {
    stubFetch([]);
    render(<Ingest />, { wrapper: makeWrapper() });
    const upload = await screen.findByRole("button", { name: strings.setupUploadAction });
    // Enabled like every other form; the empty submit names the gap.
    expect((upload as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(upload);
    expect(await screen.findByText(strings.setupUploadPickFirst)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/ingest/upload"))).toBe(false);
  });

  it("ingests one web address, with the crawl toggle in the request", async () => {
    stubFetch([
      { method: "POST", path: "/api/ingest/url", reply: respond(200, ingestUrlOk.response.body) },
    ]);
    render(<Ingest />, { wrapper: makeWrapper() });
    openTab(strings.ingestTabWebsite);
    // The crawl is a long op: Run opens the confirm gate, and the gate
    // pass runs the validation hint first.
    fireEvent.click(await screen.findByRole("button", { name: strings.runnerRunLabel }));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
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
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
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
      {
        method: "POST",
        path: "/api/ingest/directory",
        reply: respond(200, ingestDirectoryOk.response.body),
      },
    ]);
    render(<Ingest />, { wrapper: makeWrapper() });
    openTab(strings.ingestTabServerPath);
    const directoryCard = cardOf(strings.ingestTabServerPath);
    fireEvent.click(
      await within(directoryCard).findByRole("button", { name: strings.runnerRunLabel }),
    );
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    expect(await screen.findByText(strings.ingestDirRequired)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(strings.ingestDirLabel), {
      target: { value: "/data/corpus" },
    });
    fireEvent.click(within(directoryCard).getByRole("button", { name: strings.runnerRunLabel }));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    expect(await within(directoryCard).findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/ingest/directory"));
    expect(sent?.body).toContain('"directory_path":"/data/corpus"');
    expect(sent?.body).toContain('"recursive":true');
  });

  it("runs the research ingest from the server-path tab too", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/research/ingest",
        reply: respond(200, researchIngestOk.response.body),
      },
    ]);
    render(<Ingest />, { wrapper: makeWrapper() });
    openTab(strings.ingestTabServerPath);
    const researchCard = cardOf(strings.scaleResearchTitle);
    fireEvent.change(await screen.findByPlaceholderText(strings.scaleResearchPathLabel), {
      target: { value: "/data/corpus/**" },
    });
    fireEvent.click(
      within(researchCard).getByRole("button", { name: strings.runnerRunLabel }),
    );
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    expect(await within(researchCard).findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/research/ingest"));
    expect(sent?.body).toContain('"path":"/data/corpus/**"');
    expect(sent?.body).toContain('"force":false');
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

  it("shows the queue tab with its form and the polled entries", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/queue/status",
        reply: respond(200, queueStatusOk.response.body),
      },
    ]);
    render(<Ingest />, { wrapper: makeWrapper() });
    openTab(strings.ingestTabQueue);
    expect(await screen.findByText(strings.queueFormTitle)).toBeTruthy();
    expect(screen.getByLabelText(strings.queuePathLabel, { exact: false })).toBeTruthy();
    // The status card answers the first poll with the active rows, and
    // the status tags carry plain words, not the server's enum words.
    expect(await screen.findByText("A History of Networks")).toBeTruthy();
    expect(screen.getAllByText(strings.queueStatusRunning).length).toBe(1);
    expect(screen.getAllByText(strings.queueStatusPending).length).toBe(1);
  });

  it("keeps an unknown queue status word honest instead of guessing", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/queue/status",
        reply: respond(200, {
          status: "success",
          entries: [
            {
              id: "eeee4444-5555-4f66-9a77-88889999bbbb",
              path: "/data/books/maps.pdf",
              title: null,
              status: "frozen",
              attempts: 0,
              max_attempts: 3,
              last_error: null,
              elapsed_seconds: null,
              queued_at: "2026-10-28T12:00:00Z",
              run_id: null,
              project_id: null,
              pages_done: null,
              pages_total: null,
              chunks: null,
            },
          ],
          count: 1,
        }),
      },
    ]);
    render(<Ingest />, { wrapper: makeWrapper(true, ["/ingest?tab=queue"]) });
    // A word the app cannot map renders itself — never a wrong plain word.
    expect(await screen.findByText("frozen")).toBeTruthy();
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

  it("restores the tab from the address query", async () => {
    stubFetch([]);
    render(<Ingest />, { wrapper: makeWrapper(true, ["/ingest?tab=queue"]) });
    expect(await screen.findByText(strings.queueFormTitle)).toBeTruthy();
  });

  it("falls back to the first tab when the query word is unknown", async () => {
    stubFetch([]);
    render(<Ingest />, { wrapper: makeWrapper(true, ["/ingest?tab=bogus"]) });
    expect(
      await screen.findByText(strings.setupUploadCap.replace("{mb}", "50")),
    ).toBeTruthy();
  });

  it("writes the active tab into the address query", async () => {
    stubFetch([]);
    render(
      <>
        <Ingest />
        <QueryProbe />
      </>,
      { wrapper: makeWrapper() },
    );
    openTab(strings.ingestTabQueue);
    expect(await screen.findByText(strings.queueFormTitle)).toBeTruthy();
    expect(query).toBe("?tab=queue");
    openTab(strings.ingestTabFiles);
    expect(query).toBe("");
  });
  /** Drop one fake file into the Upload card's hidden file input. */
  function pickFile(name: string, sizeOverride?: number): void {
    const file = new File(["content"], name, { type: "text/plain" });
    if (sizeOverride !== undefined) {
      Object.defineProperty(file, "size", { value: sizeOverride });
    }
    const input = document.querySelector('input[type="file"]');
    if (input === null) {
      throw new Error("no file input rendered");
    }
    Object.defineProperty(input, "files", { value: [file] });
    fireEvent.change(input);
  }

  it("uploads the picked file and shows the ingest receipt", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/ingest/upload",
        reply: respond(200, { status: "success", result: { chunks: 4 } }),
      },
    ]);
    render(<Ingest />, { wrapper: makeWrapper() });
    await screen.findByRole("button", { name: strings.setupUploadAction });
    pickFile("notes.txt");
    fireEvent.click(screen.getByRole("button", { name: strings.setupUploadAction }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/ingest/upload"));
    expect(sent?.method).toBe("POST");
    expect(sent?.body.length).toBeGreaterThan(0);
  });

  it("refuses an over-cap file with the exact hint and no call", async () => {
    stubFetch([]);
    render(<Ingest />, { wrapper: makeWrapper() });
    await screen.findByRole("button", { name: strings.setupUploadAction });
    pickFile("big.bin", 51 * 1024 * 1024 + 1);
    fireEvent.click(screen.getByRole("button", { name: strings.setupUploadAction }));
    expect(
      await screen.findByText(strings.setupUploadTooLarge.replace("{mb}", "50")),
    ).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/ingest/upload"))).toBe(false);
  });

  it("lands a failed upload on the error panel with its message", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/ingest/upload",
        reply: respond(500, { status: "error", message: "The write failed." }),
      },
    ]);
    render(<Ingest />, { wrapper: makeWrapper() });
    await screen.findByRole("button", { name: strings.setupUploadAction });
    pickFile("notes.txt");
    fireEvent.click(screen.getByRole("button", { name: strings.setupUploadAction }));
    expect(await screen.findByText(strings.errorTitle)).toBeTruthy();
    expect(await screen.findByText("The write failed.")).toBeTruthy();
  });
});
