import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getEntry } from "../../api/manifest";
import { strings } from "../../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../../test/testUtils";
import type { MinimalResponse } from "../../test/testUtils";
import chunksOk from "../../test/fixtures/chunks-ok-200.json";
import DrawerAnalyze, { METHOD_OPS } from "./DrawerAnalyze";

/**
 * The drawer's NLP panel (Phase-2 Wave 5): the chunk window read scopes
 * every run, the picker keeps the six methods, the scoped body carries
 * the document's chunk ids, and a typed failure lands inline.
 */

const DOC_ID = "d1d11111-2222-4333-8444-555566667777";
const CHUNK_IDS = chunksOk.response.body.result.map((row) => row.chunk_id);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

function chunkRoutes(
  chunksReply: MinimalResponse,
  methodReplies: { path: string; reply: MinimalResponse }[] = [],
): { method: string; path: string; reply: MinimalResponse }[] {
  return [
    ...methodReplies.map((row) => ({ method: "POST", ...row })),
    { method: "GET", path: `/api/documents/${DOC_ID}/chunks`, reply: chunksReply },
  ];
}

describe("DrawerAnalyze", () => {
  it("runs a method on the document's chunks and shows the answer inline", async () => {
    stubFetch(
      chunkRoutes(respond(200, chunksOk.response.body), [
        {
          path: "/api/analysis/sentiment-screen",
          reply: respond(200, {
            status: "success",
            predictions: [{ chunk_id: CHUNK_IDS[0], label: "neutral", probability: 0.6 }],
            screened_count: 2,
          }),
        },
      ]),
    );
    render(<DrawerAnalyze docId={DOC_ID} />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(strings.reviewAnalyzeScopeAll.replace("{n}", "2")),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.method === "POST");
    expect(sent?.url).toContain("/api/analysis/sentiment-screen");
    expect(JSON.parse(sent?.body ?? "{}")).toEqual({ chunk_ids: CHUNK_IDS });
  });

  it("notes the cap honestly when the document has more chunks", async () => {
    const many = Array.from({ length: 51 }, (_, index) => ({
      chunk_id: `c0000000-0000-4000-8000-${String(index).padStart(12, "0")}`,
      doc_id: DOC_ID,
      chunk_index: index,
      text: `chunk ${index}`,
      chunk_type: null,
      start_line: null,
      end_line: null,
    }));
    stubFetch(
      chunkRoutes(respond(200, { status: "success", result: many }), [
        {
          path: "/api/analysis/sentiment-screen",
          reply: respond(200, { status: "success", predictions: [], screened_count: 50 }),
        },
      ]),
    );
    render(<DrawerAnalyze docId={DOC_ID} />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(strings.reviewAnalyzeScopeCapped.replace("{n}", "50")),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    await screen.findByText(strings.receiptDone);
    const sent = calls.find((c) => c.method === "POST");
    const body = JSON.parse(sent?.body ?? "{}");
    expect(body.chunk_ids).toHaveLength(50);
  });

  it("keeps an unavailable method honest inline", async () => {
    stubFetch(
      chunkRoutes(respond(200, chunksOk.response.body), [
        {
          path: "/api/analysis/sentiment-screen",
          reply: respond(503, {
            status: "unavailable",
            error: "The vector space is not built yet.",
            remediation: "Re-embed the corpus first.",
          }),
        },
      ]),
    );
    render(<DrawerAnalyze docId={DOC_ID} />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.unavailableTitle)).toBeTruthy();
    expect(screen.getByText("The vector space is not built yet.")).toBeTruthy();
  });

  it("shows a typed failure inline instead of a fake success", async () => {
    stubFetch(
      chunkRoutes(respond(200, chunksOk.response.body), [
        {
          path: "/api/analysis/sentiment-screen",
          reply: respond(400, {
            status: "error",
            error: "The request body is not valid.",
            error_type: "validation",
          }),
        },
      ]),
    );
    render(<DrawerAnalyze docId={DOC_ID} />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText("The request body is not valid.")).toBeTruthy();
    expect(screen.queryByText(strings.receiptDone)).toBeNull();
  });

  it("requires at least one NLI label before the run goes out", async () => {
    stubFetch(chunkRoutes(respond(200, chunksOk.response.body)));
    render(<DrawerAnalyze docId={DOC_ID} />, { wrapper: makeWrapper() });
    fireEvent.click(
      await screen.findByRole("button", { name: strings.analyzeOpNli }),
    );
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.reviewAnalyzeLabelsRequired)).toBeTruthy();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.change(screen.getByLabelText(`${strings.reviewAnalyzeLabelsLabel} *`), {
      target: { value: "policy, grid" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const sent = calls.find((c) => c.method === "POST");
    expect(JSON.parse(sent?.body ?? "{}")).toEqual({
      chunk_ids: CHUNK_IDS,
      labels: ["policy", "grid"],
    });
  });

  it("says when the document has no chunks to analyze", async () => {
    stubFetch(chunkRoutes(respond(200, { status: "success", result: [] })));
    render(<DrawerAnalyze docId={DOC_ID} />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.reviewAnalyzeNoChunks)).toBeTruthy();
    expect(screen.queryByRole("button", { name: strings.runnerRunLabel })).toBeNull();
  });

  it("falls back on a drifted chunks payload", async () => {
    stubFetch(chunkRoutes(respond(200, { status: "success", result: [{ nope: true }] })));
    render(<DrawerAnalyze docId={DOC_ID} />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.unreadableResponse)).toBeTruthy();
  });

  it("carries no longRunning picker op, so the plain run path is the whole story", () => {
    // The manifest classification pin (Wave-5 review): the dead long-run
    // branch was dropped from RunPane because NO picker op is longRunning.
    // If this pin fails, a long op joined the picker — bring the long-run
    // path back (useLongOp + LongOpControls, the OpRunner pattern).
    for (const method of METHOD_OPS) {
      const op = getEntry(method.opId);
      expect(op, method.opId).toBeTruthy();
      expect(op?.longRunning, method.opId).toBe(false);
    }
  });
});
