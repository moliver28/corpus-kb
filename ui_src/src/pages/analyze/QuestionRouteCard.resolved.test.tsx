import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import { clearCalls, makeWrapper } from "../../test/testUtils";
import { RESOLVED, ask, routeCalls, stubBoth } from "./QuestionRouteCard.fixtures";
import QuestionRouteCard from "./QuestionRouteCard";

/**
 * The QuestionRouteCard's resolved flows: the resolved tile, the run
 * button wiring, the trace disclosure, the suggestion chips, and the
 * empty-question guard. The override and ambiguous flows live in
 * QuestionRouteCard.override.test.tsx.
 */

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

  it("marks a ranked row whose op is absent from the manifest as not wired", async () => {
    stubBoth(RESOLVED);
    render(<QuestionRouteCard onRunMethod={vi.fn()} />, { wrapper: makeWrapper(false) });
    await ask("How do I find duplicate records in my corpus?");
    // Row 12 carries a non-null op the manifest does not list.
    expect(await screen.findByText(strings.analyzeMethodNotWired)).toBeTruthy();
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
