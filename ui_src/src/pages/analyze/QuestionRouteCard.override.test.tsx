import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import { clearCalls, makeWrapper } from "../../test/testUtils";
import {
  AMBIGUOUS,
  DEGRADED_AMBIGUOUS,
  OVERRIDE_05,
  READING_A,
  READING_B,
  RESOLVED,
  ask,
  routeCalls,
  stubBoth,
  stubSequential,
} from "./QuestionRouteCard.fixtures";
import QuestionRouteCard from "./QuestionRouteCard";

/**
 * The QuestionRouteCard's override flows: the ambiguity ladder pick that
 * re-calls the op with the pick's method_id, the native button keyboard
 * contract, the degraded envelope that keeps its ladder, and the
 * override-select highlight rules. The resolved flows live in
 * QuestionRouteCard.resolved.test.tsx.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

describe("QuestionRouteCard", () => {
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
});
