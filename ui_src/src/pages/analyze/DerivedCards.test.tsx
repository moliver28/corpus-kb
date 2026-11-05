import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../../test/testUtils";
import beliefsCompareOk from "../../test/fixtures/beliefs-compare-ok-200.json";
import beliefsCurrentOk from "../../test/fixtures/beliefs-current-ok-200.json";
import beliefsPredictOk from "../../test/fixtures/beliefs-predict-ok-200.json";
import beliefsTimelineOk from "../../test/fixtures/beliefs-timeline-ok-200.json";
import conflictsListOk from "../../test/fixtures/conflicts-list-ok-200.json";
import conflictsResolveOk from "../../test/fixtures/conflicts-resolve-ok-200.json";
import conflictsScanOk from "../../test/fixtures/conflicts-scan-ok-200.json";
import statementsListOk from "../../test/fixtures/statements-list-ok-200.json";
import BeliefsCard from "./BeliefsCard";
import ConflictsCard from "./ConflictsCard";
import StatementsCard from "./StatementsCard";

/**
 * The Wave 3 derived-analysis cards from canonical fixtures: the
 * statements list (serif evidence voice on the text, mono ids), the
 * conflict scan/filter and the gated human resolve (the dialog names
 * the pair), and the four belief views. The resolve posts the verdict
 * and the reviewer name — the persisted name IS the audit trail.
 */

/** The active antd tab panel inside a card (antd 6: one mounted panel). */
function activePanel(card: HTMLElement): HTMLElement {
  const panels = Array.from(card.querySelectorAll('[role="tabpanel"]'));
  const active = panels.find((p) => p.className.includes("ant-tabs-content-active"));
  if (active === undefined) {
    throw new Error("no active tab panel found");
  }
  return active as HTMLElement;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

describe("StatementsCard", () => {
  it("lists statements with serif evidence text and mono ids", async () => {
    stubFetch([
      { method: "POST", path: "/api/statements/list", reply: respond(200, statementsListOk.response.body) },
    ]);
    render(<StatementsCard />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(
        "Participants described the weekly syncs as the main source of project clarity.",
      ),
    ).toBeTruthy();
    // The evidence voice: statement text carries the serif class.
    const textCell = screen
      .getByText("Participants described the weekly syncs as the main source of project clarity.")
      .closest("span");
    expect(textCell?.className).toContain("ui-citation-text");
    // Machine values stay mono: template id and the supersede state word.
    expect(screen.getByText("sentiment-positive")).toBeTruthy();
    expect(screen.getByText(strings.analyzeStatementsSuperseded)).toBeTruthy();
    expect(screen.getAllByText(strings.analyzeStatementsCurrent).length).toBeGreaterThan(0);
    const sent = calls.find((c) => c.url.includes("/api/statements/list"));
    expect(sent?.body).toContain('"limit":50');
  });

  it("runs the history view with the typed words", async () => {
    stubFetch([
      { method: "POST", path: "/api/statements/list", reply: respond(200, statementsListOk.response.body) },
      { method: "POST", path: "/api/statements/timeline", reply: respond(200, statementsListOk.response.body) },
    ]);
    render(<StatementsCard />, { wrapper: makeWrapper() });
    const root = await screen.findByText(strings.analyzeStatementsCardTitle);
    fireEvent.click(
      within(root.closest(".ant-card") as HTMLElement).getByRole("tab", {
        name: strings.analyzeStatementsTabTimeline,
      }),
    );
    const input = screen.getByLabelText(strings.analyzeStatementsQueryLabel);
    fireEvent.change(input, { target: { value: "syncs" } });
    fireEvent.click(screen.getByRole("button", { name: strings.analyzeStatementsTimelineRun }));
    expect(await screen.findAllByText(/weekly syncs/)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/statements/timeline"));
    expect(sent?.body).toContain('"text_query":"syncs"');
  });
});

describe("ConflictsCard", () => {
  it("lists the flags with verdict and settle state", async () => {
    stubFetch([
      { method: "POST", path: "/api/conflicts/list", reply: respond(200, conflictsListOk.response.body) },
    ]);
    render(<ConflictsCard />, { wrapper: makeWrapper() });
    expect(await screen.findByText("tooling rollout")).toBeTruthy();
    expect(screen.getByText("22222222 / 11111111")).toBeTruthy();
    expect(screen.getByText(strings.analyzeConflictsOpen)).toBeTruthy();
    // The settled tag joins the verdict and the recorded reviewer name
    // (the audit trail) in one line.
    expect(screen.getByText(/dismissed/)).toBeTruthy();
    expect(screen.getByText(/parity-reviewer/)).toBeTruthy();
  });

  it("runs the scan and shows the honest pair counts", async () => {
    stubFetch([
      { method: "POST", path: "/api/conflicts/scan", reply: respond(200, conflictsScanOk.response.body) },
      { method: "POST", path: "/api/conflicts/list", reply: respond(200, conflictsListOk.response.body) },
    ]);
    render(<ConflictsCard />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.analyzeConflictsScanButton }));
    expect(await screen.findByText("Checked 12 statement pairs.")).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/conflicts/scan"));
    expect(sent?.method).toBe("POST");
  });

  it("gates the resolve write and names the pair in the dialog", async () => {
    stubFetch([
      { method: "POST", path: "/api/conflicts/list", reply: respond(200, conflictsListOk.response.body) },
      { method: "POST", path: "/api/conflicts/resolve", reply: respond(200, conflictsResolveOk.response.body) },
    ]);
    render(<ConflictsCard />, { wrapper: makeWrapper() });
    const openRow = await screen.findByText("22222222 / 11111111");
    const row = openRow.closest("tr");
    fireEvent.click(
      within(row as HTMLElement).getByRole("button", { name: strings.analyzeConflictsResolveButton }),
    );
    // The editor's gate button starts disabled: the act stays closed
    // until the reviewer name is typed (the name lands on the record).
    const gateButton = await screen.findByRole("dialog").catch(() => null);
    expect(gateButton).toBeNull();
    const settleButtons = screen.getAllByRole("button", {
      name: strings.analyzeConflictsResolveButton,
    });
    const gate = settleButtons.find((b) => b.hasAttribute("disabled"));
    expect(gate).toBeTruthy();
    // The required mark joins the label ("Your name *"), so the query
    // is a prefix match on the lockstep string.
    fireEvent.change(
      screen.getByLabelText(new RegExp(strings.analyzeConflictsReviewerLabel)),
      { target: { value: "parity-reviewer" } },
    );
    expect(gate?.hasAttribute("disabled")).toBe(false);
    fireEvent.click(gate as HTMLElement);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/22222222-2222-4222-8222-222222222222/)).toBeTruthy();
    expect(within(dialog).getByText(/Revision/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
    // An ok write closes the editor; the refreshed row carries the
    // persisted verdict and reviewer name — the record IS the audit.
    await waitFor(() => {
      expect(screen.queryByText(strings.analyzeConflictsResolveCard)).toBeNull();
    });
    const sent = calls.find((c) => c.url.includes("/api/conflicts/resolve"));
    expect(sent?.body).toContain('"conflict_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"');
    expect(sent?.body).toContain('"resolved_verdict":"revision"');
    expect(sent?.body).toContain('"resolved_by":"parity-reviewer"');
  });
});

describe("BeliefsCard", () => {
  it("shows the current posteriors with percents and evidence counts", async () => {
    stubFetch([
      { method: "POST", path: "/api/beliefs/current", reply: respond(200, beliefsCurrentOk.response.body) },
    ]);
    render(<BeliefsCard />, { wrapper: makeWrapper() });
    expect(await screen.findByText("weekly syncs")).toBeTruthy();
    // alpha 3, beta 1 -> 75%; the second row reads 33%.
    expect(screen.getByText("75%")).toBeTruthy();
    expect(screen.getByText("33%")).toBeTruthy();
  });

  it("runs the era history and the predict view from the triple", async () => {
    stubFetch([
      { method: "POST", path: "/api/beliefs/current", reply: respond(200, beliefsCurrentOk.response.body) },
      { method: "POST", path: "/api/beliefs/timeline", reply: respond(200, beliefsTimelineOk.response.body) },
      { method: "POST", path: "/api/beliefs/predict", reply: respond(200, beliefsPredictOk.response.body) },
    ]);
    render(<BeliefsCard />, { wrapper: makeWrapper() });
    const root = await screen.findByText(strings.analyzeBeliefsCardTitle);
    const card = root.closest(".ant-card") as HTMLElement;
    fireEvent.click(
      within(card).getByRole("tab", { name: strings.analyzeBeliefsTabTimeline }),
    );
    const panel = activePanel(card);
    fireEvent.change(
      within(panel).getByLabelText(strings.analyzeBeliefsSubjectLabel),
      { target: { value: "weekly syncs" } },
    );
    fireEvent.change(
      within(panel).getByLabelText(strings.analyzeBeliefsRelationLabel),
      { target: { value: "helps" } },
    );
    fireEvent.change(
      within(panel).getByLabelText(strings.analyzeBeliefsObjectLabel),
      { target: { value: "project clarity" } },
    );
    fireEvent.click(within(panel).getByRole("button", { name: strings.analyzeBeliefsRunLabel }));
    expect(
      await screen.findByText(strings.analyzeBeliefsPosteriorLine.replace("{mean}", "75%")),
    ).toBeTruthy();
    const timeline = calls.find((c) => c.url.includes("/api/beliefs/timeline"));
    expect(timeline?.body).toContain('"subject":"weekly syncs"');
    // The predict view reads from its own inputs in its own panel.
    fireEvent.click(
      within(card).getByRole("tab", { name: strings.analyzeBeliefsTabPredict }),
    );
    const predictPanel = activePanel(card);
    fireEvent.click(
      within(predictPanel).getByRole("button", { name: strings.analyzeBeliefsRunLabel }),
    );
    expect(
      await screen.findByText(strings.analyzeBeliefsPredictLine.replace("{prob}", "75%")),
    ).toBeTruthy();
    expect(
      screen.getByText(
        strings.analyzeBeliefsIntervalWord.replace("{low}", "31%").replace("{high}", "95%"),
      ),
    ).toBeTruthy();
  });

  it("compares one subject's beliefs across objects", async () => {
    stubFetch([
      { method: "POST", path: "/api/beliefs/current", reply: respond(200, beliefsCurrentOk.response.body) },
      { method: "POST", path: "/api/beliefs/compare", reply: respond(200, beliefsCompareOk.response.body) },
    ]);
    render(<BeliefsCard />, { wrapper: makeWrapper() });
    const root = await screen.findByText(strings.analyzeBeliefsCardTitle);
    const card = root.closest(".ant-card") as HTMLElement;
    fireEvent.click(within(card).getByRole("tab", { name: strings.analyzeBeliefsTabCompare }));
    const panel = activePanel(card);
    fireEvent.change(
      within(panel).getByLabelText(strings.analyzeBeliefsSubjectLabel),
      { target: { value: "weekly syncs" } },
    );
    fireEvent.click(within(panel).getByRole("button", { name: strings.analyzeBeliefsRunLabel }));
    expect(await screen.findByText("helps")).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/beliefs/compare"));
    expect(sent?.body).toContain('"subject":"weekly syncs"');
  });
});
