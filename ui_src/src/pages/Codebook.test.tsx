import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { MinimalResponse } from "../test/testUtils";
import codebooksOk from "../test/fixtures/codebooks-ok-200.json";
import cycleHalted from "../test/fixtures/cycle-halted-200.json";
import doctorOk from "../test/fixtures/doctor-ok.json";
import projectsOk from "../test/fixtures/projects-ok-200.json";
import promoteOk from "../test/fixtures/promote-ok-200.json";
import proposalsOk from "../test/fixtures/proposals-ok-200.json";
import Codebook from "./Codebook";

/**
 * The Codebook page from canonical fixtures: the pre-flight reads, the
 * promote gate that stays shut until the name and the definition carry
 * text, the cycle wizard's plain-English halt outcome, and the honest
 * banners for the unwired pipeline steps.
 */

const CYCLE_BODY = '{"mode":"in"}';

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

function codebookRoutes() {
  return [
    { method: "GET", path: "/api/projects", reply: respond(200, projectsOk.response.body) },
    { method: "GET", path: "/api/codebooks", reply: respond(200, codebooksOk.response.body) },
    { method: "GET", path: "/api/doctor", reply: respond(200, doctorOk.response.body) },
    { method: "GET", path: "/api/codebook/proposals", reply: respond(200, proposalsOk.response.body) },
  ];
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  clearCalls();
});

describe("Codebook page", () => {
  it("renders the pre-flight reads with the version table", async () => {
    stubFetch(codebookRoutes());
    render(<Codebook />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(strings.codebookProjectsLine.replace("{count}", "1")),
    ).toBeTruthy();
    expect(await screen.findByText("Grid interviews v2")).toBeTruthy();
    // The doctor fixture carries no dimension rows: the honest line shows.
    expect(screen.getByText(strings.codebookDimsNone)).toBeTruthy();
  });

  it("keeps the promote gate shut until the name and the definition carry text", async () => {
    stubFetch([
      ...codebookRoutes(),
      { method: "POST", path: "/api/codebook/promote", reply: respond(200, promoteOk.response.body) },
    ]);
    render(<Codebook />, { wrapper: makeWrapper() });
    const promoteButtons = await screen.findAllByRole("button", {
      name: strings.codebookPromoteAction,
    });
    fireEvent.click(promoteButtons[0]);
    const dialog = await screen.findByRole("dialog");
    const textboxes = within(dialog).getAllByRole("textbox");
    const nameInput = textboxes.find((el) => el.tagName === "INPUT")!;
    const definition = textboxes.find((el) => el.tagName === "TEXTAREA")!;
    // Nothing is pre-seeded: the first pass asks for the name, then the
    // definition, and only a typed pair unlocks the op.
    fireEvent.click(within(dialog).getByRole("button", { name: strings.codebookPromoteAction }));
    expect(await screen.findByText(strings.codebookPromoteNameRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/codebook/promote"))).toBe(false);
    fireEvent.change(nameInput, { target: { value: "Storm outage blame" } });
    fireEvent.click(within(dialog).getByRole("button", { name: strings.codebookPromoteAction }));
    expect(await screen.findByText(strings.codebookPromoteDefinitionRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/codebook/promote"))).toBe(false);
    fireEvent.change(definition, {
      target: { value: "Blames storm damage for outage delays." },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: strings.codebookPromoteAction }));
    expect(await screen.findByText(strings.codebookPromoteDone)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/codebook/promote"))).toBe(true);
  });

  it("shows the duplicate block with its merge suggestion", async () => {
    stubFetch([
      ...codebookRoutes(),
      {
        method: "POST",
        path: "/api/codebook/promote",
        reply: respond(200, {
          status: "duplicate_blocked",
          proposal_id: 12,
          tau_dup: 0.9,
          max_cos: 0.93,
          merge_suggestion: "grid_reliability",
        }),
      },
    ]);
    render(<Codebook />, { wrapper: makeWrapper() });
    const promoteButtons = await screen.findAllByRole("button", {
      name: strings.codebookPromoteAction,
    });
    fireEvent.click(promoteButtons[0]);
    const dialog = await screen.findByRole("dialog");
    const textboxes = within(dialog).getAllByRole("textbox");
    fireEvent.change(textboxes.find((el) => el.tagName === "INPUT")!, {
      target: { value: "Storm outage blame" },
    });
    fireEvent.change(textboxes.find((el) => el.tagName === "TEXTAREA")!, {
      target: { value: "Blames storm damage for outage delays." },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: strings.codebookPromoteAction }));
    expect(await screen.findByText(strings.codebookPromoteDuplicate)).toBeTruthy();
    expect(screen.getAllByText(/grid_reliability/).length).toBeGreaterThan(0);
  });

  it("maps a halted cycle to its plain-English outcome", async () => {
    vi.useFakeTimers();
    try {
      let release!: (reply: MinimalResponse) => void;
      const gate = new Promise<MinimalResponse>((resolve) => {
        release = resolve;
      });
      // One impl: the cycle holds on the gate promise; the pre-flight
      // reads answer from the recorded routes.
      const cycleBodies: string[] = [];
      const impl = (url: string | URL, init?: { method?: string; body?: unknown }): Promise<MinimalResponse> => {
        if (String(url).includes("/api/research/cycle")) {
          cycleBodies.push(String(init?.body ?? ""));
          return gate;
        }
        const method = (init?.method ?? "GET").toUpperCase();
        const hit = codebookRoutes().find((r) => r.method === method && String(url).includes(r.path));
        if (hit) return Promise.resolve(hit.reply);
        return Promise.reject(new Error(`unexpected fetch: ${method} ${String(url)}`));
      };
      vi.stubGlobal("fetch", impl as unknown as typeof fetch);
      render(<Codebook />, { wrapper: makeWrapper() });
      const cycleCard = cardOf(strings.codebookCycleTitle);
      fireEvent.click(
        within(cycleCard).getByRole("button", { name: strings.codebookCycleAction }),
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      const dialog = screen.getByRole("dialog");
      fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      release({ status: 200, text: async () => JSON.stringify(cycleHalted.response.body) });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(20);
      });
      expect(within(cycleCard).getByText(strings.codebookCycleHalted)).toBeTruthy();
      expect(within(cycleCard).getAllByText(/codebook_promotion/).length).toBeGreaterThan(0);
      // The safety rule, pinned: the wizard asks for one more stage and
      // for nothing else — approve can never ride along.
      expect(cycleBodies).toEqual([CYCLE_BODY]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("marks the unwired pipeline steps honestly", async () => {
    stubFetch(codebookRoutes());
    render(<Codebook />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.codebookEmbedNotWired)).toBeTruthy();
    expect(screen.getByText(strings.codebookPoolNotWired)).toBeTruthy();
    expect(screen.getByText(strings.codebookBatchNotWired)).toBeTruthy();
    expect(screen.getByText(strings.codebookCalibrateNotWired)).toBeTruthy();
    expect(screen.getByText(strings.codebookReliabilityNotWired)).toBeTruthy();
    expect(screen.getByText(strings.codebookSaturationNotWired)).toBeTruthy();
  });
});
