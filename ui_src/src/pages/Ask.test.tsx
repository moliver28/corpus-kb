import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { strings } from "../strings";
import askDegraded from "../test/fixtures/ask-degraded-200.json";
import askEmpty from "../test/fixtures/ask-empty-200.json";
import askSuccess from "../test/fixtures/ask-success-200.json";
import notebookAnswer from "../test/fixtures/notebook-answer-200.json";
import searchOk from "../test/fixtures/search-ok-200.json";
import Ask from "./Ask";

/**
 * The Ask page from canonical fixtures: the cited answer with its
 * verified badge, the exact program strings for the empty and degraded
 * states, the search tab with the degraded-excluded count, the notebook
 * tab with its retrieval-only toggle, and the honest verify gap.
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

function askRoutes(reply: MinimalResponse) {
  return [{ method: "POST", path: "/api/ask", reply }];
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  calls.length = 0;
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
    expect(screen.getAllByText(/samples\/grid-brief\.md/).length).toBeGreaterThan(0);
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
    // The switcher and the pane's submit share the "Search" name; the
    // switcher renders first, the submit last.
    const switcher = (
      await screen.findAllByRole("button", { name: strings.askTabSearch })
    )[0];
    fireEvent.click(switcher);
    fireEvent.change(await screen.findByPlaceholderText(strings.askSearchQueryLabel), {
      target: { value: "storage" },
    });
    const searchButtons = screen.getAllByRole("button", { name: strings.askSearchAction });
    fireEvent.click(searchButtons[searchButtons.length - 1]);
    expect(
      await screen.findByText("The plan installs battery storage at two substations."),
    ).toBeTruthy();
    expect(screen.getByText("0.91")).toBeTruthy();
    expect(screen.getByText(strings.askDegradedExcluded.replace("{count}", "1"))).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/search"));
    expect(sent?.body).toContain('"query":"storage"');
  });

  it("asks the notebook with the retrieval-only toggle on", async () => {
    stubFetch([
      { method: "POST", path: "/api/notebook/ask", reply: respond(200, notebookAnswer.response.body) },
    ]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabNotebook }));
    fireEvent.change(await screen.findByPlaceholderText(strings.askNotebookProjectLabel), {
      target: { value: "11111111-2222-4333-8444-555566667777" },
    });
    // Two "Question" inputs exist now (the ask card and the notebook
    // card); the notebook's is the second in the DOM.
    const questionInputs = screen.getAllByPlaceholderText(strings.askQueryLabel);
    fireEvent.change(questionInputs[questionInputs.length - 1], {
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
    expect(sent?.body).toContain('"retrieval_only":true');
  });

  it("shows the honest gap on the verify tab", async () => {
    stubFetch([]);
    render(<Ask />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.askTabVerify }));
    expect(await screen.findByText(strings.askVerifyNotWired)).toBeTruthy();
  });
});
