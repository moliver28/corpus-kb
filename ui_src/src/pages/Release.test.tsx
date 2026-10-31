import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import releaseAssessOk from "../test/fixtures/release-assess-ok-200.json";
import releaseBlocked409 from "../test/fixtures/release-blocked-409.json";
import releaseStatusOk from "../test/fixtures/release-status-ok-200.json";
import Release from "./Release";

/**
 * The Release page from canonical fixtures: the assess gate table, the
 * status read over the query-string GET, the gated execute, and the
 * structured 409 refusal that lists the unmet required gates.
 */

function cardOf(title: string): HTMLElement {
  const card = screen
    .getAllByText(title)
    .map((el) => el.closest(".ant-card"))
    .find((c) => c !== null && c.querySelector(".ant-card-head-title")?.textContent === title);
  if (card === undefined) {
    throw new Error(`no card found for ${title}`);
  }
  return card as HTMLElement;
}

function fill(card: HTMLElement, placeholder: string, value: string): void {
  fireEvent.change(within(card).getByPlaceholderText(placeholder), {
    target: { value },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

describe("Release page", () => {
  it("assesses and renders the gate table with the required failures", async () => {
    stubFetch([
      { method: "POST", path: "/api/release/assess", reply: respond(200, releaseAssessOk.response.body) },
    ]);
    render(<Release />, { wrapper: makeWrapper() });
    const assessCard = cardOf(strings.releaseAssessTitle);
    fill(assessCard, strings.releaseVersionLabel, "aaaa1111-2222-4333-8444-555566667777");
    fireEvent.click(within(assessCard).getByRole("button", { name: strings.runnerRunLabel }));
    expect(await within(assessCard).findByText(strings.releaseGatesHeading)).toBeTruthy();
    expect(within(assessCard).getByText(/Coverage 0.91 clears the floor./)).toBeTruthy();
    expect(
      within(assessCard).getByText(
        `${strings.releaseRequiredFailing} g_precision`,
      ),
    ).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/release/assess"));
    expect(sent?.body).toContain('"codebook_version_id":"aaaa1111-2222-4333-8444-555566667777"');
  });

  it("reads the status over the query-string GET", async () => {
    stubFetch([
      { method: "GET", path: "/api/release/status", reply: respond(200, releaseStatusOk.response.body) },
    ]);
    render(<Release />, { wrapper: makeWrapper() });
    const statusCard = cardOf(strings.releaseStatusTitle);
    fill(statusCard, strings.releaseVersionLabel, "aaaa1111-2222-4333-8444-555566667777");
    fireEvent.click(within(statusCard).getByRole("button", { name: strings.runnerRunLabel }));
    expect(
      await within(statusCard).findByText(
        "Improve precision or waive g_precision, then execute.",
      ),
    ).toBeTruthy();
    const poll = calls.find((c) => c.url.includes("/api/release/status"));
    expect(poll?.url).toContain("codebook_version_id=aaaa1111-2222-4333-8444-555566667777");
  });

  it("renders the blocked execute as the structured refusal", async () => {
    stubFetch([
      { method: "POST", path: "/api/release/release", reply: respond(409, releaseBlocked409.response.body) },
    ]);
    render(<Release />, { wrapper: makeWrapper() });
    const executeCard = cardOf(strings.releaseExecuteTitle);
    fill(executeCard, strings.releaseVersionLabel, "aaaa1111-2222-4333-8444-555566667777");
    fill(executeCard, strings.releaseReleaseLabel, "7777cccc-8888-4ddd-9eee-5555ffff6666");
    fill(executeCard, strings.releaseExecuteApproverLabel, "Alex");
    fireEvent.click(within(executeCard).getByRole("button", { name: strings.runnerRunLabel }));
    // The gate dialog shows every value before the confirm.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Alex/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
    expect(await within(executeCard).findByText(strings.releaseBlockedTitle)).toBeTruthy();
    expect(within(executeCard).getByText("g_precision")).toBeTruthy();
    expect(
      within(executeCard).getByText(
        "required gates must be pass or waived before release",
      ),
    ).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/release/release"));
    expect(sent?.body).toContain('"approver":"Alex"');
  });

  it("asks for the required fields before any release call", async () => {
    stubFetch([]);
    render(<Release />, { wrapper: makeWrapper() });
    const requestCard = cardOf(strings.releaseRequestTitle);
    fireEvent.click(within(requestCard).getByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.formRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/release/request"))).toBe(false);
  });
});
