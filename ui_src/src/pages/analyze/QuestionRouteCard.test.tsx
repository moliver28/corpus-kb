import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import {
  calls,
  clearCalls,
  makeWrapper,
  respond,
  stubFetch,
  stubFetchImpl,
} from "../../test/testUtils";
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
    {
      method_id: "12",
      name: "Cluster structure inspector",
      why: "The rank rides the runner-up.",
      run_hint: { op: "cluster_texts", params: {} },
    },
  ],
  trace: {
    mode: "phrase",
    threshold: 0.55,
    margin: 0.1,
    scores: [{ method_id: "29", score: 0.9 }],
  },
  suggestions: [
    "Which near duplicates are genuinely separate speakers?",
    "How do thresholds split review from merge?",
    "What paraphrases survived the exact-match pass?",
  ],
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

/** The override answer a picked reading routes to (the pick's method_id). */
const OVERRIDE_05: Record<string, unknown> = {
  status: "resolved",
  intent: {
    question: "how do i group and code these records?",
    status: "resolved",
    confidence: 0.0,
    method_id: "05",
    note: "expert_override",
    readings: [],
    ranked_method_ids: ["05", "01"],
  },
  ranked: [
    {
      method_id: "05",
      name: "Cluster method",
      why: "Set by the analyst.",
      run_hint: { op: "cluster_texts", params: {} },
    },
    {
      method_id: "01",
      name: "Codebook method",
      why: READING_B.why,
      run_hint: { op: null, params: {} },
    },
  ],
  suggestions: ["Which cluster count fits these interviews?"],
  trace: { mode: "phrase", threshold: 0.55, margin: 0.1, scores: [] },
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

/** First route post answers `first`, later ones answer `second` (the
    pick's re-route lands as a different envelope shape). */
function stubSequential(
  first: Record<string, unknown>,
  second: Record<string, unknown>,
): void {
  let routePosts = 0;
  stubFetchImpl((url: string | URL, init?: { method?: string; body?: unknown }) => {
    const method = (init?.method ?? "GET").toUpperCase();
    const path = String(url);
    const known =
      method === "POST" && (path.includes("/api/analysis/route") || path.includes("/api/analysis/select-method"));
    if (!known) {
      return Promise.reject(new Error(`unexpected fetch: ${method} ${path}`));
    }
    calls.push({ method, url: path, body: String(init?.body ?? "") });
    if (path.includes("/api/analysis/select-method")) {
      return Promise.resolve(respond(200, methodList()));
    }
    routePosts += 1;
    return Promise.resolve(respond(200, routePosts === 1 ? first : second));
  });
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

  it("picks a reading, re-calls with its method_id, and renders the override answer", async () => {
    stubSequential(AMBIGUOUS, OVERRIDE_05);
    const onRun = vi.fn();
    render(<QuestionRouteCard onRunMethod={onRun} />, { wrapper: makeWrapper(false) });
    await ask("How do I group and code these records?");
    expect(await screen.findByText(strings.routeStatusAmbiguous)).toBeTruthy();
    // Ambiguous: no method is chosen, so the chips stay absent and the
    // ladder is the answer.
    expect(screen.queryByText(strings.routeSuggestionsTitle)).toBeNull();
    expect(screen.getByText(strings.routeReadingsTitle)).toBeTruthy();
    expect(screen.getByText(strings.routeReadingsHint)).toBeTruthy();
    // Exact counts pin the layout: the why renders once on the
    // top-why line, once on its ranked row, once in the picker (A);
    // B renders on its ranked row and in the picker (2).
    expect(screen.getAllByText(READING_A.why).length).toBe(3);
    expect(screen.getAllByText(READING_B.why).length).toBe(2);
    // The picker is a labelled group of native toggle buttons (the
    // override-picker idiom): each names its method, so the accessible
    // text carries id AND name. Native buttons are tab stops and
    // Enter or Space activates the focused one, so the keyboard model
    // matches what the group role promises.
    const group = screen.getByRole("group", { name: strings.routeReadingsPickLabel });
    const pick05 = within(group).getByRole("button", { name: /^05 / });
    expect(pick05 instanceof HTMLButtonElement).toBe(true);
    expect(pick05.getAttribute("aria-pressed")).toBe("false");
    // Confirm-then-run: the pick alone sends nothing.
    fireEvent.click(pick05);
    await act(async () => {
      await Promise.resolve();
    });
    expect(routeCalls().length).toBe(1);
    // Re-query after the flush: a re-render replaces the button node.
    const regroup = screen.getByRole("group", { name: strings.routeReadingsPickLabel });
    expect(
      within(regroup).getByRole("button", { name: /^05 / }).getAttribute("aria-pressed"),
    ).toBe("true");
    // The "Use this reading" action re-calls the op with the pick.
    fireEvent.click(screen.getByRole("button", { name: strings.routeUseReading }));
    await act(async () => {
      await Promise.resolve();
    });
    const sends = routeCalls();
    expect(sends.length).toBe(2);
    expect(sends[1].body).toContain('"method_id":"05"');
    // The re-route lands as an override answer, rendered honestly.
    expect(await screen.findByText(strings.routeStatusResolved)).toBeTruthy();
    expect(screen.getByText(strings.routeOverrideNote)).toBeTruthy();
    expect(screen.getAllByText("Set by the analyst.").length).toBeGreaterThanOrEqual(1);
    // The chosen method's chips replace the ladder.
    expect(screen.getByText(strings.routeSuggestionsTitle)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Which cluster count fits these interviews?" }),
    ).toBeTruthy();
    expect(screen.queryByRole("group", { name: strings.routeReadingsPickLabel })).toBeNull();
  });

  it("gives every reading control native button semantics for keyboard use", async () => {
    stubBoth(AMBIGUOUS);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I group and code these records?");
    const group = await screen.findByRole("group", { name: strings.routeReadingsPickLabel });
    const controls = within(group).getAllByRole("button");
    expect(controls.length).toBe(2);
    // A native button is a tab stop, and the browser synthesizes a
    // click on Enter or Space. That native model is the keyboard
    // contract the toggle-group idiom promises; a div with a role
    // would need hand-rolled key handling to keep the same promise.
    for (const control of controls) {
      expect(control instanceof HTMLButtonElement).toBe(true);
    }
  });

  it("renders two identical suggestion chips without a key collision", async () => {
    const duplicated: Record<string, unknown> = {
      ...RESOLVED,
      suggestions: [
        "What comes after this screening step?",
        "What comes after this screening step?",
      ],
    };
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      stubBoth(duplicated);
      render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
      await ask("How do I find duplicate records in my corpus?");
      const chips = await screen.findAllByRole("button", {
        name: "What comes after this screening step?",
      });
      expect(chips.length).toBe(2);
      // Both chips share their display text, so React must not see a
      // duplicate key (the composite key carries the index).
      const keyWarnings = errorSpy.mock.calls.filter((call) =>
        call.some((arg) => typeof arg === "string" && /same key|duplicate keys/i.test(arg)),
      );
      expect(keyWarnings).toEqual([]);
    } finally {
      errorSpy.mockRestore();
    }
  });

  it("keeps the ambiguity ladder on a degraded envelope (addendum grammar)", async () => {
    stubBoth(DEGRADED_AMBIGUOUS);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I group and code these records?");
    expect(await screen.findByText(strings.routeStatusDegraded)).toBeTruthy();
    expect(screen.getByText(strings.routeDegradedNote)).toBeTruthy();
    // Degraded + ambiguous: the ladder stays (keyed off intent.status);
    // same three-two split as the plain ambiguous envelope.
    expect(screen.getByText(strings.routeReadingsTitle)).toBeTruthy();
    expect(screen.getAllByText(READING_A.why).length).toBe(3);
    expect(screen.getAllByText(READING_B.why).length).toBe(2);
  });

  it("marks a ranked row whose op is absent from the manifest as not wired", async () => {
    stubBoth(RESOLVED);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I find duplicate records in my corpus?");
    // Row 12 carries a non-null op the manifest does not list.
    expect(await screen.findByText(strings.analyzeMethodNotWired)).toBeTruthy();
  });

  it("does not light the override highlight when the guard trips", async () => {
    // Clear-textarea-then-click-override: the empty-question guard runs
    // BEFORE the highlight moves, so nothing lights up and nothing sends.
    stubBoth(RESOLVED);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I find duplicate records in my corpus?");
    await screen.findByText(strings.routeStatusResolved);
    fireEvent.change(screen.getByLabelText(strings.routeQuestionLabel), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^01 / }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(routeCalls().length).toBe(1);
    expect(screen.getByText(strings.routeQuestionRequired)).toBeTruthy();
    expect(screen.getByRole("button", { name: /^01 / }).getAttribute("aria-pressed")).toBe(
      "false",
    );
  });

  it("refuses to route an empty question", async () => {
    stubBoth(RESOLVED);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    fireEvent.click(
      await screen.findByRole(
        "button",
        { name: strings.routeQuestionAction },
        { timeout: 5_000 },
      ),
    );
    expect(await screen.findByText(strings.routeQuestionRequired)).toBeTruthy();
    expect(routeCalls().length).toBe(0);
  });

  it("clears the stale override highlight on a plain re-route", async () => {
    stubBoth(AMBIGUOUS);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I group and code these records?");
    expect(await screen.findByText(strings.routeStatusAmbiguous)).toBeTruthy();
    // The 30-method override picker lights its picked button. Scope to
    // the override group: the reading ladder's toggle buttons share the
    // /^01 / accessible name, so an unscoped query matches twice.
    const overrideGroup = () =>
      within(screen.getByRole("group", { name: strings.routeOverrideLabel }));
    fireEvent.click(overrideGroup().getByRole("button", { name: /^01 / }));
    await act(async () => {
      await Promise.resolve();
    });
    const sends = routeCalls();
    expect(sends.length).toBe(2);
    expect(sends[1].body).toContain('"method_id":"01"');
    // Re-query: the re-render replaces the button node (R14).
    expect(overrideGroup().getByRole("button", { name: /^01 / }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    // ...and a plain Route press (no method_id) clears the highlight.
    // jsdom never finishes antd's loading-icon leave motion, so the
    // accessible name keeps a stale "loading " prefix (R13).
    fireEvent.click(screen.getByRole("button", { name: /^(loading )?Route$/ }));
    await act(async () => {
      await Promise.resolve();
    });
    const clears = routeCalls();
    expect(clears.length).toBe(3);
    expect(clears[2].body).not.toContain("method_id");
    expect(overrideGroup().getByRole("button", { name: /^01 / }).getAttribute("aria-pressed")).toBe(
      "false",
    );
  });

  it("fills the question box from a suggestion chip without sending", async () => {
    stubBoth(RESOLVED);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I find duplicate records in my corpus?");
    expect(await screen.findByText(strings.routeSuggestionsTitle)).toBeTruthy();
    expect(screen.getByText(strings.routeSuggestionsHint)).toBeTruthy();
    // The chip's accessible name is the suggestion text itself.
    const chip = screen.getByRole("button", {
      name: "How do thresholds split review from merge?",
    });
    fireEvent.click(chip);
    await act(async () => {
      await Promise.resolve();
    });
    // The chip fills the persisted box; the analyst still presses Route.
    const box = screen.getByLabelText(strings.routeQuestionLabel) as HTMLTextAreaElement;
    expect(box.value).toBe("How do thresholds split review from merge?");
    expect(routeCalls().length).toBe(1);
  });
});
