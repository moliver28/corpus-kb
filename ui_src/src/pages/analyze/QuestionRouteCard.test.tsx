import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../../test/testUtils";
import QuestionRouteCard from "./QuestionRouteCard";

/**
 * The question card from canned envelopes: the resolved tile with the
 * always-visible one-line why, the read-only ambiguity ladder, the
 * degraded status that still shows its ladder (the addendum grammar),
 * the collapsed trace, and the override select that re-calls the op
 * with the chosen method_id.
 */

const RESOLVED: Record<string, unknown> = {
  status: "resolved",
  intent: {
    question: "how do i find duplicate records?",
    status: "resolved",
    confidence: 0.9,
    method_id: "29",
    note: null,
    readings: [],
  },
  ranked: [
    {
      method_id: "29",
      name: "Near-duplicate and paraphrase detection",
      why: "Find duplicate or paraphrased records before analysis.",
      run_hint: { op: "duplicates_screen", params: {} },
    },
    {
      method_id: "04",
      name: "Dictionary or rule-based classification",
      why: "All axes are equal. The id order decides.",
      run_hint: { op: null, params: {} },
    },
  ],
  trace: {
    mode: "phrase",
    threshold: 0.55,
    margin: 0.1,
    scores: [{ method_id: "29", score: 0.9 }],
  },
};

const READING_A = {
  method_id: "05",
  method_name: "Cluster method",
  why: "How do I cluster my documents by theme?",
};
const READING_B = {
  method_id: "01",
  method_name: "Codebook method",
  why: "How do I build a codebook for my interviews?",
};

const AMBIGUOUS: Record<string, unknown> = {
  status: "ambiguous",
  intent: { status: "ambiguous", note: null, method_id: null },
  ranked: [
    {
      method_id: "05",
      name: "Cluster method",
      why: READING_A.why,
      run_hint: { op: null, params: {} },
    },
    {
      method_id: "01",
      name: "Codebook method",
      why: READING_B.why,
      run_hint: { op: null, params: {} },
    },
  ],
  interpretations: [READING_A, READING_B],
  trace: { mode: "phrase", threshold: 0.55, margin: 0.1, scores: [] },
};

/** Degraded envelope with an ambiguous intent: the ladder must stay. */
const DEGRADED_AMBIGUOUS: Record<string, unknown> = {
  ...AMBIGUOUS,
  status: "degraded",
  intent: { status: "ambiguous", note: "degraded", method_id: null },
};

/** Thirty minimal register rows (the override select's source). */
function methodList(count = 30): Record<string, unknown> {
  return {
    status: "success",
    register_version: "1.1.0",
    recommendations: Array.from({ length: count }, (_, index) => ({
      id: String(index + 1).padStart(2, "0"),
      name: `Method ${index + 1}`,
      summary: "A test row.",
      complexity_tier: "baseline",
      rank: index + 1,
      rank_reason: "Test order.",
      op: null,
    })),
  };
}

function stubBoth(routeBody: Record<string, unknown>): void {
  stubFetch([
    { method: "POST", path: "/api/analysis/route", reply: respond(200, routeBody) },
    { method: "POST", path: "/api/analysis/select-method", reply: respond(200, methodList()) },
  ]);
}

function routeCalls(): { url: string; body: string }[] {
  return calls
    .filter((c) => c.method === "POST" && c.url.includes("/api/analysis/route"))
    .map((c) => ({ url: c.url, body: c.body }));
}

async function ask(question: string): Promise<void> {
  fireEvent.change(await screen.findByPlaceholderText(strings.routeQuestionPlaceholder), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: strings.routeQuestionAction }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

describe("QuestionRouteCard", () => {
  it("shows the resolved tile with the always-visible one-line why", async () => {
    stubBoth(RESOLVED);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I find duplicate records in my corpus?");
    expect(await screen.findByText(strings.routeStatusResolved)).toBeTruthy();
    // The why renders twice by design: the always-on top-why line and
    // the ranked row. At least one must be present. (The method name
    // rides a composite text node, so text queries cannot match it.)
    expect(
      (await screen.findAllByText("Find duplicate or paraphrased records before analysis."))
        .length,
    ).toBeGreaterThanOrEqual(1);
    const sent = routeCalls()[0];
    expect(sent.body).toContain('"question":"How do I find duplicate records in my corpus?"');
  });

  it("renders the run button for a wired op and the human note for a null op", async () => {
    stubBoth(RESOLVED);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I find duplicate records in my corpus?");
    expect(await screen.findByRole("button", { name: strings.analyzeRunMethod })).toBeTruthy();
    expect(screen.getByText(strings.analyzeHumanOnly)).toBeTruthy();
  });

  it("opens the trace disclosure with mode, threshold, margin, and scores", async () => {
    stubBoth(RESOLVED);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I find duplicate records in my corpus?");
    const summary = await screen.findByText(
      strings.routeTraceSummary.replace("{mode}", "phrase")
        .replace("{threshold}", "0.55")
        .replace("{margin}", "0.1"),
    );
    fireEvent.click(summary);
    // antd 6 names the expanded panel body .ant-collapse-body.
    const score = await screen.findByText("0.9");
    const panel = score.closest(".ant-collapse-body");
    if (panel === null) {
      throw new Error("trace panel did not expand");
    }
    // The score row shows the method id and its cosine.
    expect(within(panel as HTMLElement).getByText("29")).toBeTruthy();
    expect(within(panel as HTMLElement).getByText("0.9")).toBeTruthy();
  });

  it("lists the readings read-only and re-calls with the picked method_id", async () => {
    stubBoth(AMBIGUOUS);
    const onRun = vi.fn();
    render(<QuestionRouteCard onRunMethod={onRun} />, { wrapper: makeWrapper(false) });
    await ask("How do I group and code these records?");
    expect(await screen.findByText(strings.routeStatusAmbiguous)).toBeTruthy();
    expect(screen.getByText(strings.routeReadingsTitle)).toBeTruthy();
    expect(screen.getByText(strings.routeReadingsHint)).toBeTruthy();
    // The reading restatements render in the ladder (ranked rows repeat
    // the names; the whys here are the ladder's own).
    expect(screen.getAllByText(READING_B.why).length).toBeGreaterThanOrEqual(1);
    // The override picker is populated from the register listing (the
    // stub's rows); clicking an id re-routes with that method_id.
    fireEvent.click(screen.getByRole("button", { name: "01" }));
    await act(async () => {
      await Promise.resolve();
    });
    const sends = routeCalls();
    expect(sends.length).toBe(2);
    expect(sends[1].body).toContain('"method_id":"01"');
  });

  it("keeps the ambiguity ladder on a degraded envelope (addendum grammar)", async () => {
    stubBoth(DEGRADED_AMBIGUOUS);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I group and code these records?");
    expect(await screen.findByText(strings.routeStatusDegraded)).toBeTruthy();
    expect(screen.getByText(strings.routeDegradedNote)).toBeTruthy();
    // Degraded + ambiguous: the ladder stays (keyed off intent.status).
    expect(screen.getByText(strings.routeReadingsTitle)).toBeTruthy();
    expect(screen.getAllByText(READING_A.why).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(READING_B.why).length).toBeGreaterThanOrEqual(1);
  });

  it("refuses to route an empty question", async () => {
    stubBoth(RESOLVED);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    fireEvent.click(await screen.findByRole("button", { name: strings.routeQuestionAction }));
    expect(await screen.findByText(strings.routeQuestionRequired)).toBeTruthy();
    expect(routeCalls().length).toBe(0);
  });
});
