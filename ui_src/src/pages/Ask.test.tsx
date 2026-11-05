import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { MinimalResponse } from "../test/testUtils";
import askDegraded from "../test/fixtures/ask-degraded-200.json";
import askEmpty from "../test/fixtures/ask-empty-200.json";
import askSuccess from "../test/fixtures/ask-success-200.json";
import notebookAnswer from "../test/fixtures/notebook-answer-200.json";
import projectsOk from "../test/fixtures/projects-ok-200.json";
import searchContextOk from "../test/fixtures/search-context-ok-200.json";
import searchOk from "../test/fixtures/search-ok-200.json";
import searchSimilarOk from "../test/fixtures/search-similar-ok-200.json";
import unavailable503 from "../test/fixtures/unavailable-503.json";
import verifyAbstained from "../test/fixtures/verify-abstained-200.json";
import verifyOk from "../test/fixtures/verify-ok-200.json";
import Ask from "./Ask";

/**
 * The Ask page from canonical fixtures: the cited answer with its
 * verified badge, the exact program strings for the empty and degraded
 * states, the search tab with its three modes (text, find-similar,
 * context), the verify tab with its verdicts and honest states, the
 * notebook tab with its project picker and retrieval-only toggle, and
 * the URL-synced switcher (default, restore, write rows).
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

function askRoutes(reply: MinimalResponse) {
  return [{ method: "POST", path: "/api/ask", reply }];
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  // The clipboard stub rides on navigator; reset it for the next row.
  Object.defineProperty(navigator, "clipboard", {
    value: undefined,
    configurable: true,
  });
  clearCalls();
  query = "";
});

async function askQuestion(question: string): Promise<void> {
  fireEvent.change(await screen.findByPlaceholderText(strings.askQueryLabel), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: strings.askAction }));
}

describe("Ask page", () => {
  it("answers with cited sentences, the badge, and the session history", async () => {
    stubFetch(askRoutes(respond(200, askSuccess.response.body)));
    const { container } = render(<Ask />, { wrapper: makeWrapper() });
    await askQuestion("what does the grid plan do?");
    expect(
      await screen.findByText(
        "The grid plan adds storage. Storage smooths evening peaks [1] [2].",
      ),
    ).toBeTruthy();
    expect(screen.getByText(strings.verifyLabelVerified)).toBeTruthy();
    // Citations show the basename; the full local path rides the title
    // attribute (and the raw JSON view), never the visible line.
    expect(screen.getAllByText(/grid-brief\.md/).length).toBeGreaterThan(0);
    expect(screen.getAllByTitle(/corpus/).length).toBeGreaterThan(0);
    expect(screen.getByText("what does the grid plan do?")).toBeTruthy();
    expect(window.sessionStorage.getItem("corpus-kb:ask-history")).toContain(
      "what does the grid plan do?",
    );
    // The hover-citation link: hovering sentence 1 raises it (active)
    // and marks sentence 2, which cites the same source, as linked.
    const sentences = container.querySelectorAll("ol.ui-citations > li");
    expect(sentences.length).toBe(2);
    fireEvent.mouseEnter(sentences[0]);
    expect(sentences[0].className).toContain("ui-citation--active");
    expect(sentences[1].className).toContain("ui-citation--linked");
    fireEvent.mouseLeave(container.querySelector("ol.ui-citations")!);
    expect(sentences[1].className).not.toContain("ui-citation--linked");
  });

  it("stops waiting on cancel: elapsed ticks, the result never lands", async () => {
    vi.useFakeTimers();
    try {
      // The server answer waits on a gate, so the flight stays running
      // until the user cancels — then the late reply must be discarded.
      let release!: (reply: MinimalResponse) => void;
      const gate = new Promise<MinimalResponse>((resolve) => {
        release = resolve;
      });
      vi.stubGlobal("fetch", (() => gate) as unknown as typeof fetch);
      render(<Ask />, { wrapper: makeWrapper() });
      fireEvent.change(screen.getByPlaceholderText(strings.askQueryLabel), {
        target: { value: "slow question" },
      });
      fireEvent.click(screen.getByRole("button", { name: strings.askAction }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(
        screen.getByText(strings.runnerRunningElapsed.replace("{sec}", "1")),
      ).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: strings.runnerCancelLabel }));
      release({ status: 200, text: async () => JSON.stringify(askSuccess.response.body) });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });
      expect(screen.getByText(strings.runnerCancelled)).toBeTruthy();
      expect(screen.queryAllByText("The grid plan adds storage.").length).toBe(0);
      expect(window.sessionStorage.getItem("corpus-kb:ask-history")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the exact empty-state words when no evidence matched", async () => {
    stubFetch(askRoutes(respond(200, askEmpty.response.body)));
    render(<Ask />, { wrapper: makeWrapper() });
    await askQuestion("what taxes are listed?");
    expect(await screen.findByText(strings.askEmptyState)).toBeTruthy();
  });

  it("shows the sources without an answer when generation degraded", async () => {
    stubFetch(askRoutes(respond(200, askDegraded.response.body)));
    render(<Ask />, { wrapper: makeWrapper() });
    await askQuestion("what does the plan install?");
    expect(await screen.findByText(strings.askDegradedState)).toBeTruthy();
    expect(
      screen.getByText("The plan installs battery storage at two substations."),
    ).toBeTruthy();
    expect(screen.queryByText(strings.verifyLabelVerified)).toBeNull();
    expect(screen.getByText(strings.askDegradedExcluded.replace("{count}", "2"))).toBeTruthy();
  });

  it("refuses an empty question without a network call", async () => {
    stubFetch(askRoutes(respond(200, askSuccess.response.body)));
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askAction }));
    expect(await screen.findByText(strings.askQueryRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/ask"))).toBe(false);
  });

  it("searches and shows the degraded-excluded count", async () => {
    stubFetch([{ method: "POST", path: "/api/search", reply: respond(200, searchOk.response.body) }]);
    render(<Ask />, { wrapper: makeWrapper() });
        const switcher = await screen.findByRole("button", { name: strings.askTabSearch });
    fireEvent.click(switcher);
    fireEvent.change(await screen.findByPlaceholderText(strings.askSearchQueryLabel), {
      target: { value: "storage" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchAction }));
    expect(
      await screen.findByText("The plan installs battery storage at two substations."),
    ).toBeTruthy();
    expect(screen.getByText("0.91")).toBeTruthy();
    // The chunk id shows on the row: it is the value the verify tab asks for.
    expect(screen.getByText("1111aaaa-2222-4bbb-8ccc-3333dddd4444")).toBeTruthy();
    expect(screen.getByText(strings.askDegradedExcluded.replace("{count}", "1"))).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/search"));
    expect(sent?.body).toContain('"query":"storage"');
  });

  it("switches to find-similar: no stale rows, then the similar hits", async () => {
    // Specific routes first: the stub matches by substring, and the
    // similar path contains the plain search path.
    stubFetch([
      {
        method: "POST",
        path: "/api/search/similar",
        reply: respond(200, searchSimilarOk.response.body),
      },
      { method: "POST", path: "/api/search", reply: respond(200, searchOk.response.body) },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    const switcher = await screen.findByRole("button", { name: strings.askTabSearch });
    fireEvent.click(switcher);
    fireEvent.change(await screen.findByPlaceholderText(strings.askSearchQueryLabel), {
      target: { value: "storage" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchAction }));
    expect(
      await screen.findByText("The plan installs battery storage at two substations."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchModeSimilar }));
    expect(
      screen.queryByText("The plan installs battery storage at two substations."),
    ).toBeNull();
    fireEvent.change(await screen.findByPlaceholderText(strings.askSearchChunkLabel), {
      target: { value: "7777cccc-8888-4ddd-9eee-5555ffff6666" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchAction }));
    expect(await screen.findByText("Crews restored the feeder after the storm.")).toBeTruthy();
    expect(screen.getByText("0.88")).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/search/similar"));
    expect(sent?.body).toContain('"chunk_id":"7777cccc-8888-4ddd-9eee-5555ffff6666"');
  });

  it("searches with context chunks in the context mode", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/search/context",
        reply: respond(200, searchContextOk.response.body),
      },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    const switcher = await screen.findByRole("button", { name: strings.askTabSearch });
    fireEvent.click(switcher);
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchModeContext }));
    fireEvent.change(await screen.findByPlaceholderText(strings.askSearchQueryLabel), {
      target: { value: "storage" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchAction }));
    expect(
      await screen.findByText("The grid plan adds storage before the winter peak."),
    ).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/search/context"));
    expect(sent?.body).toContain('"query":"storage"');
  });

  it("shows the honest empty state when similar finds nothing", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/search/similar",
        reply: respond(200, { status: "success", result: [] }),
      },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    const switcher = await screen.findByRole("button", { name: strings.askTabSearch });
    fireEvent.click(switcher);
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchModeSimilar }));
    fireEvent.change(await screen.findByPlaceholderText(strings.askSearchChunkLabel), {
      target: { value: "7777cccc-8888-4ddd-9eee-5555ffff6666" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchAction }));
    expect(await screen.findByText(strings.askSearchEmpty)).toBeTruthy();
  });

  it("refuses a similar search without a chunk id", async () => {
    stubFetch([]);
    render(<Ask />, { wrapper: makeWrapper() });
    const switcher = await screen.findByRole("button", { name: strings.askTabSearch });
    fireEvent.click(switcher);
    // The empty search text names its own field, never the ask question.
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchAction }));
    expect(await screen.findByText(strings.askSearchQueryRequired)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchModeSimilar }));
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchAction }));
    expect(await screen.findByText(strings.askSearchChunkRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/search/similar"))).toBe(false);
  });

  it("copies a row's chunk id with a visible note, then resets it", async () => {
    stubFetch([{ method: "POST", path: "/api/search", reply: respond(200, searchOk.response.body) }]);
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<Ask />, { wrapper: makeWrapper() });
    const switcher = await screen.findByRole("button", { name: strings.askTabSearch });
    fireEvent.click(switcher);
    fireEvent.change(await screen.findByPlaceholderText(strings.askSearchQueryLabel), {
      target: { value: "storage" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchAction }));
    const copyButtons = await screen.findAllByRole("button", {
      name: strings.askCopyChunkId,
    });
    expect(copyButtons.length).toBeGreaterThan(0);
    fireEvent.click(copyButtons[0]);
    expect(writeText).toHaveBeenCalledWith("1111aaaa-2222-4bbb-8ccc-3333dddd4444");
    expect(await screen.findByText(strings.copiedNote)).toBeTruthy();
  });

  it("caps the search table page and expands a row for the full text", async () => {
    const rows = Array.from({ length: 12 }, (_, index) => ({
      chunk_id: `chunk-${String(index + 1).padStart(2, "0")}`,
      source: `/data/doc-${String(index + 1).padStart(2, "0")}.md`,
      text: `Sentence ${String(index + 1).padStart(2, "0")} about storage.`,
      score: 0.5,
    }));
    stubFetch([
      {
        method: "POST",
        path: "/api/search",
        reply: respond(200, { status: "success", result: rows }),
      },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    const switcher = await screen.findByRole("button", { name: strings.askTabSearch });
    fireEvent.click(switcher);
    fireEvent.change(await screen.findByPlaceholderText(strings.askSearchQueryLabel), {
      target: { value: "storage" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askSearchAction }));
    await screen.findByText("chunk-01");
    // Page one only: the rest wait behind the pager.
    expect(screen.queryByText("chunk-11")).toBeNull();
    // The long text previews with an ellipsis; the full text rides the
    // cell's title and the row expands on demand.
    const preview = document.querySelector(".ant-table-tbody .ant-table-cell-ellipsis");
    expect(preview?.getAttribute("title")).toBe("Sentence 01 about storage.");
    const expandIcon = document.querySelector(".ant-table-row-expand-icon");
    expect(expandIcon?.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(document.querySelector(".ant-pagination-item-2")!);
    expect(screen.getByText("chunk-11")).toBeTruthy();
    // Back to page one: paging remounts the rows, so re-query the icon.
    fireEvent.click(document.querySelector(".ant-pagination-item-1")!);
    const rowIcon = document.querySelector(".ant-table-row-expand-icon");
    fireEvent.click(rowIcon!);
    expect(rowIcon?.getAttribute("aria-expanded")).toBe("true");
  });

  it("routes the notebook's error envelope to the error panel, not info", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/projects",
        reply: respond(200, projectsOk.response.body),
      },
      {
        method: "POST",
        path: "/api/notebook/ask",
        reply: respond(200, { status: "error", message: "The project id is unknown." }),
      },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabNotebook }));
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByText("Grid Interviews"));
    fireEvent.change(screen.getByLabelText(strings.askNotebookQuestionLabel, { exact: false }), {
      target: { value: "what caused the outage?" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askNotebookAction }));
    // The typed error surface names the failure; the message never
    // dresses up as an information banner.
    expect(await screen.findByText(strings.errorTitle)).toBeTruthy();
    expect(screen.getByText("The project id is unknown.").closest(".ui-banner")).toBeNull();
  });

  it("clears the question history from the card", async () => {
    stubFetch(askRoutes(respond(200, askSuccess.response.body)));
    render(<Ask />, { wrapper: makeWrapper() });
    await askQuestion("what does the grid plan do?");
    expect(await screen.findByText("what does the grid plan do?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: strings.askHistoryClear }));
    expect(screen.queryByText("what does the grid plan do?")).toBeNull();
    expect(screen.getByText(strings.askHistoryEmpty)).toBeTruthy();
    expect(window.sessionStorage.getItem("corpus-kb:ask-history")).toBe("[]");
  });

  it("checks an answer and shows the verdicts with the badge", async () => {
    stubFetch([
      { method: "POST", path: "/api/verify", reply: respond(200, verifyOk.response.body) },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabVerify }));
    fireEvent.change(await screen.findByPlaceholderText(strings.askVerifyAnswerLabel), {
      target: { value: "The plan adds storage. Storage smooths evening peaks." },
    });
    fireEvent.change(await screen.findByPlaceholderText(strings.askVerifyChunksLabel), {
      target: { value: "1111aaaa-2222-4bbb-8ccc-3333dddd4444" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askVerifyAction }));
    expect(await screen.findByText("The plan adds storage.")).toBeTruthy();
    expect(screen.getByText(strings.askVerdictEntailed)).toBeTruthy();
    expect(screen.getByText(strings.askVerdictUnsupported)).toBeTruthy();
    expect(screen.getByText(strings.claimSupportLabel.replace("{pct}", "50"))).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/verify"));
    expect(sent?.body).toContain(
      '"answer":"The plan adds storage. Storage smooths evening peaks."',
    );
    expect(sent?.body).toContain('"chunk_ids":["1111aaaa-2222-4bbb-8ccc-3333dddd4444"]');
  });

  it("shows the honest abstained note when the check cannot judge", async () => {
    stubFetch([
      { method: "POST", path: "/api/verify", reply: respond(200, verifyAbstained.response.body) },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabVerify }));
    fireEvent.change(await screen.findByPlaceholderText(strings.askVerifyAnswerLabel), {
      target: { value: "The plan adds storage." },
    });
    fireEvent.change(await screen.findByPlaceholderText(strings.askVerifyChunksLabel), {
      target: { value: "1111aaaa-2222-4bbb-8ccc-3333dddd4444" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askVerifyAction }));
    expect(await screen.findByText(strings.askVerifyAbstained)).toBeTruthy();
  });

  it("falls back to the raw view when the verify payload drifted", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/verify",
        reply: respond(200, { status: "success", result: { verdicts: "all-good" } }),
      },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabVerify }));
    fireEvent.change(await screen.findByPlaceholderText(strings.askVerifyAnswerLabel), {
      target: { value: "The plan adds storage." },
    });
    fireEvent.change(await screen.findByPlaceholderText(strings.askVerifyChunksLabel), {
      target: { value: "1111aaaa-2222-4bbb-8ccc-3333dddd4444" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askVerifyAction }));
    expect(await screen.findByText(strings.unreadableResponse)).toBeTruthy();
    expect(screen.getByText(strings.receiptRaw)).toBeTruthy();
  });

  it("refuses an empty verify form without a network call", async () => {
    stubFetch([]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabVerify }));
    fireEvent.click(screen.getByRole("button", { name: strings.askVerifyAction }));
    expect(await screen.findByText(strings.askVerifyAnswerRequired)).toBeTruthy();
    expect(screen.getByText(strings.askVerifyChunksRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/verify"))).toBe(false);
  });

  it("asks the notebook with the retrieval-only toggle on", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/projects",
        reply: respond(200, projectsOk.response.body),
      },
      { method: "POST", path: "/api/notebook/ask", reply: respond(200, notebookAnswer.response.body) },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabNotebook }));
    // The project comes from the picker, not a typed UUID.
    fireEvent.mouseDown(screen.getByRole("combobox"));
    fireEvent.click(await screen.findByText("Grid Interviews"));
    // The notebook question carries its own label, unique on the page.
    fireEvent.change(screen.getByLabelText(strings.askNotebookQuestionLabel, { exact: false }), {
      target: { value: "what caused the outage?" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: strings.askNotebookRetrievalOnly }),
    );
    fireEvent.click(screen.getByRole("button", { name: strings.askNotebookAction }));
    expect(
      await screen.findByText("Residents blamed storm damage for the outage [1]."),
    ).toBeTruthy();
    expect(screen.getAllByText(/Outage interview 1/).length).toBeGreaterThan(0);
    const sent = calls.find((c) => c.url.includes("/api/notebook/ask"));
    expect(sent?.body).toContain('"project_id":"11111111-2222-4333-8444-555566667777"');
    expect(sent?.body).toContain('"retrieval_only":true');
  });

  it("clears a stale notebook field error when the field is corrected", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/projects",
        reply: respond(200, projectsOk.response.body),
      },
      { method: "POST", path: "/api/notebook/ask", reply: respond(200, notebookAnswer.response.body) },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabNotebook }));
    // One empty submit marks both fields (the project stays unpicked).
    fireEvent.click(screen.getByRole("button", { name: strings.askNotebookAction }));
    expect(screen.getByText(strings.askNotebookProjectRequired)).toBeTruthy();
    expect(screen.getByText(strings.askQueryRequired)).toBeTruthy();
    // Fill the question only and resubmit: the question's error must go.
    fireEvent.change(screen.getByLabelText(strings.askNotebookQuestionLabel, { exact: false }), {
      target: { value: "what caused the outage?" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askNotebookAction }));
    expect(screen.queryByText(strings.askQueryRequired)).toBeNull();
    expect(screen.getByText(strings.askNotebookProjectRequired)).toBeTruthy();
    // The failed submit never reached the server.
    expect(calls.some((c) => c.url.includes("/api/notebook/ask"))).toBe(false);
  });

  it("falls back to the typed project id when the project list fails", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/projects",
        reply: respond(503, unavailable503.response.body),
      },
      { method: "POST", path: "/api/notebook/ask", reply: respond(200, notebookAnswer.response.body) },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabNotebook }));
    // The failed read keeps its error panel and the typed-id path.
    expect(await screen.findByText(strings.unavailableTitle)).toBeTruthy();
    expect(screen.getByText(strings.askProjectsFallback)).toBeTruthy();
    fireEvent.change(await screen.findByPlaceholderText(strings.askNotebookProjectLabel), {
      target: { value: "11111111-2222-4333-8444-555566667777" },
    });
    fireEvent.change(screen.getByLabelText(strings.askNotebookQuestionLabel, { exact: false }), {
      target: { value: "what caused the outage?" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.askNotebookAction }));
    expect(
      await screen.findByText("Residents blamed storm damage for the outage [1]."),
    ).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/notebook/ask"));
    expect(sent?.body).toContain('"project_id":"11111111-2222-4333-8444-555566667777"');
  });

  it("shows the honest empty state when no projects exist", async () => {
    stubFetch([
      { method: "GET", path: "/api/projects", reply: respond(200, { status: "success", result: [] }) },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabNotebook }));
    expect(await screen.findByText(strings.askProjectsEmpty)).toBeTruthy();
    // The picker read ran once on mount; no typed fallback on an empty
    // ledger — the banner is the honest answer.
    expect(calls.filter((c) => c.url.includes("/api/projects")).length).toBe(1);
    expect(screen.queryByPlaceholderText(strings.askNotebookProjectLabel)).toBeNull();
  });

  it("wires the verify tab with the real check form", async () => {
    stubFetch([]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabVerify }));
    // The verify manifest row landed (issue #182): the real form is here.
    expect(screen.getByPlaceholderText(strings.askVerifyAnswerLabel)).toBeTruthy();
  });

  it("restores the verify tab from the address query", async () => {
    stubFetch([]);
    render(<Ask />, { wrapper: makeWrapper(true, ["/ask?tab=verify"]) });
    expect(
      screen.getByRole("button", { name: strings.askTabVerify }).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("falls back to the search tab when the query word is unknown", async () => {
    stubFetch([]);
    render(<Ask />, { wrapper: makeWrapper(true, ["/ask?tab=bogus"]) });
        const switcher = await screen.findByRole("button", { name: strings.askTabSearch });
    expect(switcher.getAttribute("aria-pressed")).toBe("true");
  });

  it("writes the active tab into the address query", async () => {
    stubFetch([]);
    render(
      <>
        <Ask />
        <QueryProbe />
      </>,
      { wrapper: makeWrapper() },
    );
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabVerify }));
    expect(query).toBe("?tab=verify");
  });
});
