import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { strings } from "../strings";
import ok200 from "../test/fixtures/ok-200.json";
import selectMethodOk from "../test/fixtures/select-method-ok-200.json";
import topicsTrendsDisabled from "../test/fixtures/topics-trends-disabled-200.json";
import Analyze from "./Analyze";

/**
 * The Analyze page from canonical fixtures: the advisor ranks the
 * register's methods and hands one to the runner, a long guided build
 * runs behind the confirm gate with honest elapsed time (fake timers),
 * the dormant trends feature shows its exact config key with a
 * copyable YAML snippet, and the unwired families say so.
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

function cardOf(title: string): HTMLElement {
  const card = screen.getByText(title).closest(".ant-card");
  if (card === null) {
    throw new Error(`no card found for ${title}`);
  }
  return card as HTMLElement;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  // A timed-out test never reaches its own finally; keep fake timers
  // from leaking into the next test.
  vi.useRealTimers();
  calls.length = 0;
});

describe("Analyze page", () => {
  it("ranks methods for a task and hands one to the runner", async () => {
    stubFetch([
      { method: "POST", path: "/api/analysis/select-method", reply: respond(200, selectMethodOk.response.body) },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    fireEvent.change(await screen.findByPlaceholderText(strings.analyzeAdvisorTaskLabel), {
      target: { value: "find the recurring themes in the interviews" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.analyzeAdvisorAction }));
    expect(await screen.findByText("TF-IDF keyphrase scan")).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/analysis/select-method"));
    expect(sent?.body).toContain('"task_description":"find the recurring themes in the interviews"');
    // "Run this method" pre-fills the runner with the row's manifest op.
    fireEvent.click(screen.getAllByRole("button", { name: strings.analyzeRunMethod })[0]);
    expect(
      screen.getByRole("button", { name: "keyphrases-extract" }).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("runs a picked method through the seeded, editable runner", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/analysis/sentiment-screen",
        reply: respond(200, ok200.response.body),
      },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: "sentiment-screen" }));
    const runnerCard = cardOf(strings.analyzeRunnerTitle);
    fireEvent.change(within(runnerCard).getByRole("textbox"), {
      target: { value: '{"lexicon": "preset"}' },
    });
    fireEvent.click(within(runnerCard).getByRole("button", { name: strings.runnerRunLabel }));
    expect(await within(runnerCard).findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("sentiment-screen"));
    expect(sent?.body).toContain('"lexicon":"preset"');
  });

  it("runs a long guided build behind the gate with honest elapsed time", async () => {
    vi.useFakeTimers();
    try {
      let release!: (reply: MinimalResponse) => void;
      const gate = new Promise<MinimalResponse>((resolve) => {
        release = resolve;
      });
      vi.stubGlobal("fetch", (() => gate) as unknown as typeof fetch);
      render(<Analyze />, { wrapper: makeWrapper() });
      const topicsCard = cardOf(strings.analyzeTopicsBuildTitle);
      fireEvent.click(within(topicsCard).getByRole("button", { name: strings.runnerRunLabel }));
      // The confirm gate renders its dialog in a portal; fake timers
      // freeze findBy, so flush the motion first, then query directly.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      const dialog = screen.getByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(
        within(topicsCard).getByText(
          strings.runnerRunningElapsed.replace("{sec}", "2"),
        ),
      ).toBeTruthy();
      release({ status: 200, text: async () => JSON.stringify(ok200.response.body) });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });
      expect(within(topicsCard).getByText(strings.receiptDone)).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the exact config key and copyable YAML for a dormant feature", async () => {
    stubFetch([
      { method: "POST", path: "/api/topics/trends", reply: respond(200, topicsTrendsDisabled.response.body) },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    const trendsCard = cardOf(strings.analyzeTrendsTitle);
    fireEvent.click(within(trendsCard).getByRole("button", { name: strings.runnerRunLabel }));
    expect(await within(trendsCard).findByText(strings.analyzeDormant)).toBeTruthy();
    expect(
      within(trendsCard).getByText("analysis.topics.over_time.enabled"),
    ).toBeTruthy();
    expect(within(trendsCard).getByText(/enabled: true/)).toBeTruthy();
    expect(
      within(trendsCard).getByRole("button", { name: strings.analyzeCopyYaml }),
    ).toBeTruthy();
  });

  it("marks the unwired analysis families honestly", async () => {
    stubFetch([]);
    render(<Analyze />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.analyzeStatementsNotWired)).toBeTruthy();
    expect(screen.getByText(strings.analyzeConflictsNotWired)).toBeTruthy();
    expect(screen.getByText(strings.analyzeBeliefsNotWired)).toBeTruthy();
    expect(screen.getByText(strings.analyzeEmbedMetaNotWired)).toBeTruthy();
    expect(screen.getByText(strings.analyzeExtractionTuneNotWired)).toBeTruthy();
  });
});
