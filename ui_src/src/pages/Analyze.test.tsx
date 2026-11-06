import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { MinimalResponse } from "../test/testUtils";
import classificationOk from "../test/fixtures/classification-ok-200.json";
import labelingGoldOk from "../test/fixtures/labeling-gold-ok-200.json";
import labelingListOk from "../test/fixtures/labeling-list-ok-200.json";
import labelingLfsOk from "../test/fixtures/labeling-lfs-ok-200.json";
import labelingTriageInsufficient from "../test/fixtures/labeling-triage-insufficient-200.json";
import labelingTriageOk from "../test/fixtures/labeling-triage-ok-200.json";
import ok200 from "../test/fixtures/ok-200.json";
import routeOk from "../test/fixtures/route-ok-200.json";
import selectMethodOk from "../test/fixtures/select-method-ok-200.json";
import topicsBuildDisabled from "../test/fixtures/topics-build-disabled-200.json";
import topicsTrendsDisabled from "../test/fixtures/topics-trends-disabled-200.json";
import Analyze from "./Analyze";

/**
 * The Analyze page from canonical fixtures: the advisor ranks the
 * register's methods and hands one to the runner, a long guided build
 * runs behind the confirm gate with honest elapsed time (fake timers),
 * the dormant trends feature shows its exact config key with a
 * copyable YAML snippet, and the unwired families say so.
 */

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
  clearCalls();
});

describe("Analyze page", () => {
  it("ranks methods for a task and hands one to the runner", async () => {
    stubFetch([
      { method: "POST", path: "/api/analysis/select-method", reply: respond(200, selectMethodOk.response.body) },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    // The suite runs many jsdom files in parallel workers; the default
    // 1 s async timeout flakes under that load, so these waits are wide
    // (15 s). The honest trade: a real regression surfaces only after
    // the finder's full budget, not in seconds.
    fireEvent.change(
      await screen.findByPlaceholderText(strings.analyzeAdvisorTaskLabel, {}, { timeout: 15_000 }),
      {
        target: { value: "find the recurring themes in the interviews" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: strings.analyzeAdvisorAction }));
    expect(await screen.findByText("TF-IDF keyphrase scan", {}, { timeout: 15_000 })).toBeTruthy();
    // Spec F8: the row carries its summary plus the register's own
    // validation and permitted-claims guidance (expanded by default).
    expect(
      screen.getByText(
        "Rank words and word sequences by document frequency and corpus rarity to find distinctive wording.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Review tokenization, common words, phrase lengths, and the top-ranked terms.",
      ),
    ).toBeTruthy();
    expect(
      screen.getByText("Descriptions of distinctive wording in the corpus."),
    ).toBeTruthy();
    // Task mode: the advisor's ranked call is the ONLY select-method
    // call (the question card's register listing is mode-gated).
    const sent = calls.find((c) => c.url.includes("/api/analysis/select-method"));
    expect(sent?.body).toContain('"task_description":"find the recurring themes in the interviews"');
    // "Run this method" pre-fills the runner with the row's manifest op
    // and moves focus to the runner card heading, so the handoff lands.
    fireEvent.click(screen.getAllByRole("button", { name: strings.analyzeRunMethod })[0]);
    const picker = screen.getByRole("button", {
      name: strings.analyzeOpKeyphrases,
    });
    expect(picker.getAttribute("aria-pressed")).toBe("true");
    // The human name carries the raw op id in its tooltip.
    expect(picker.getAttribute("title")).toBe("keyphrases-extract");
    expect(document.activeElement?.textContent).toBe(strings.analyzeRunnerTitle);
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
    fireEvent.click(await screen.findByRole("button", { name: strings.analyzeOpSentiment }));
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

  it("never claims Done for a non-success envelope (live walkthrough finding)", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/topics/build",
        reply: respond(200, topicsBuildDisabled.response.body),
      },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    const topicsCard = cardOf(strings.analyzeTopicsBuildTitle);
    fireEvent.click(within(topicsCard).getByRole("button", { name: strings.runnerRunLabel }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
    expect(
      await within(topicsCard).findByText(
        "topics.enabled is false in the loaded config.",
      ),
    ).toBeTruthy();
    expect(
      within(topicsCard).getByText(
        "Set topics.enabled: true (and install the [topics] extra), then retry.",
      ),
    ).toBeTruthy();
    expect(within(topicsCard).queryByText(strings.receiptDone)).toBeNull();
  });

  it("routes a plain-language question from the advisor's question mode", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/analysis/route",
        reply: respond(200, routeOk.response.body),
      },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    // Toggle to question mode; the task form yields to the router.
    fireEvent.click(await screen.findByRole("button", { name: strings.routeModeQuestion }));
    fireEvent.change(await screen.findByPlaceholderText(strings.routeQuestionPlaceholder), {
      target: { value: "How do I find duplicate records in my corpus?" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.routeQuestionAction }));
    // The why renders twice (top-why line + ranked row) by design.
    expect(
      (await screen.findAllByText("Find duplicate or paraphrased records before analysis."))
        .length,
    ).toBeGreaterThanOrEqual(1);
    expect(await screen.findByText(strings.routeStatusResolved)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/analysis/route"));
    expect(sent?.body).toContain('"question":"How do I find duplicate records in my corpus?"');
    // The runner bridge still works from a routed row's run_hint op.
    fireEvent.click(screen.getAllByRole("button", { name: strings.analyzeRunMethod })[0]);
    expect(
      screen.getByRole("button", { name: "duplicates-screen" }).getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("marks the unwired analysis ops honestly (the two with no surfaces)", async () => {
    stubFetch([]);
    render(<Analyze />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.analyzeEmbedMetaNotWired)).toBeTruthy();
    expect(screen.getByText(strings.analyzeExtractionTuneNotWired)).toBeTruthy();
  });

  it("reads the built labels and the LF registry in the labeling card", async () => {
    stubFetch([
      // The lfs route rides under the /api/labeling prefix, so it must
      // come first: the stub matches the first path hit.
      { method: "GET", path: "/api/labeling/lfs", reply: respond(200, labelingLfsOk.response.body) },
      { method: "GET", path: "/api/labeling", reply: respond(200, labelingListOk.response.body) },
      { method: "GET", path: "/api/classification", reply: respond(200, classificationOk.response.body) },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    const labelingCard = cardOf(strings.analyzeLabelingCardTitle);
    expect(await within(labelingCard).findByText("method_quality")).toBeTruthy();
    expect(within(labelingCard).getByText("87%")).toBeTruthy();
    expect(within(labelingCard).getAllByText("Jan 05, 2026").length).toBe(2);
    // The LF registry tab loads on first activation: names, coverage,
    // and the honest "not scored" cell for the stat-less row.
    fireEvent.click(within(labelingCard).getByRole("tab", { name: strings.analyzeLabelingTabLfs }));
    expect(await within(labelingCard).findByText("keyword_method")).toBeTruthy();
    expect(within(labelingCard).getByText("42%")).toBeTruthy();
    expect(within(labelingCard).getByText(strings.analyzeLfsNoStats)).toBeTruthy();
  });

  it("shows the honest empty label state when nothing is built", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/labeling",
        reply: respond(200, { status: "success", label_count: 0, labels: [] }),
      },
      {
        method: "GET",
        path: "/api/classification",
        reply: respond(200, { status: "success", prediction_count: 0, predictions: [] }),
      },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.analyzeLabelsEmpty)).toBeTruthy();
    expect(screen.getByText(strings.analyzeClassificationEmpty)).toBeTruthy();
  });

  it("runs the triage ranking and keeps it read-only advice", async () => {
    stubFetch([
      { method: "GET", path: "/api/labeling", reply: respond(200, { status: "success", label_count: 0, labels: [] }) },
      { method: "GET", path: "/api/classification", reply: respond(200, { status: "success", prediction_count: 0, predictions: [] }) },
      { method: "POST", path: "/api/labeling/triage", reply: respond(200, labelingTriageOk.response.body) },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    const labelingCard = cardOf(strings.analyzeLabelingCardTitle);
    fireEvent.click(within(labelingCard).getByRole("tab", { name: strings.analyzeLabelingTabTriage }));
    fireEvent.click(await within(labelingCard).findByRole("button", { name: strings.analyzeTriageAction }));
    // The queue rows carry the assigned and runner-up label names; the
    // runner-up word repeats across rows, so the assert counts matches.
    expect(
      (await within(labelingCard).findAllByText("outcome_report")).length,
    ).toBeGreaterThan(0);
    // The server's own success reason shows (the gold panel's one
    // treatment): flagged total plus the advisory contract.
    expect(
      within(labelingCard).getByText(
        "3/40 units below out-of-sample support 0.2; queue is advisory - never auto-relabel",
      ),
    ).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/labeling/triage"));
    expect(sent?.body).toBe("{}");
  });

  it("names the reason and remedy when triage lacks the data", async () => {
    stubFetch([
      { method: "GET", path: "/api/labeling", reply: respond(200, { status: "success", label_count: 0, labels: [] }) },
      { method: "GET", path: "/api/classification", reply: respond(200, { status: "success", prediction_count: 0, predictions: [] }) },
      { method: "POST", path: "/api/labeling/triage", reply: respond(200, labelingTriageInsufficient.response.body) },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    const labelingCard = cardOf(strings.analyzeLabelingCardTitle);
    fireEvent.click(within(labelingCard).getByRole("tab", { name: strings.analyzeLabelingTabTriage }));
    fireEvent.click(await within(labelingCard).findByRole("button", { name: strings.analyzeTriageAction }));
    expect(
      await within(labelingCard).findByText(/holds only 2 of the needed 5 labeled units/),
    ).toBeTruthy();
    expect(within(labelingCard).getByText("Label more chunks, then retry.")).toBeTruthy();
  });

  it("checks the gold set: required path, validations, both numbers", async () => {
    stubFetch([
      { method: "GET", path: "/api/labeling", reply: respond(200, { status: "success", label_count: 0, labels: [] }) },
      { method: "GET", path: "/api/classification", reply: respond(200, { status: "success", prediction_count: 0, predictions: [] }) },
      { method: "POST", path: "/api/labeling/gold-check", reply: respond(200, labelingGoldOk.response.body) },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    const labelingCard = cardOf(strings.analyzeLabelingCardTitle);
    fireEvent.click(within(labelingCard).getByRole("tab", { name: strings.analyzeLabelingTabGold }));
    // The empty path blocks the run at the field, never at the server.
    fireEvent.click(within(labelingCard).getByRole("button", { name: strings.analyzeGoldAction }));
    expect(within(labelingCard).getByText(strings.analyzeGoldFileRequired)).toBeTruthy();
    fireEvent.change(within(labelingCard).getByPlaceholderText(strings.analyzeGoldFileLabel), {
      target: { value: "/data/gold-dev.json" },
    });
    fireEvent.click(within(labelingCard).getByRole("button", { name: strings.analyzeGoldAction }));
    expect(await within(labelingCard).findByText("method_quality")).toBeTruthy();
    // Both numbers per class: raw agreement and the corrected estimate.
    expect(within(labelingCard).getByText("83%")).toBeTruthy();
    expect(within(labelingCard).getByText("79%")).toBeTruthy();
    expect(within(labelingCard).getByText("61% - 90%")).toBeTruthy();
    expect(within(labelingCard).getByText("16 gold chunks matched the build; 1 ignored.")).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/labeling/gold-check"));
    expect(sent?.body).toContain('"gold_file":"/data/gold-dev.json"');
  });

  it("lists the built classifications with both probabilities", async () => {
    stubFetch([
      { method: "GET", path: "/api/labeling", reply: respond(200, { status: "success", label_count: 0, labels: [] }) },
      { method: "GET", path: "/api/classification", reply: respond(200, classificationOk.response.body) },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    const card = cardOf(strings.analyzeClassificationReadTitle);
    expect(await within(card).findByText("tier_a")).toBeTruthy();
    expect(within(card).getByText("91%")).toBeTruthy();
    expect(within(card).getByText("88%")).toBeTruthy();
    // The uncalibrated row reads "-", never a fake number.
    expect(within(card).getByText(strings.analyzeClassificationReviewTag)).toBeTruthy();
  });
  it("keeps a drifted labels payload on the warn-plus-raw path", async () => {
    // The guard fails on a row without its fields: the honest unreadable
    // banner and the raw JSON, never a broken table. The Wave 3 family
    // cards reject their reads on this stub, so the assertions scope to
    // the labeling card.
    stubFetch([
      {
        method: "GET",
        path: "/api/labeling",
        reply: respond(200, { status: "success", labels: [{ chunk_id: 7 }] }),
      },
      {
        method: "GET",
        path: "/api/classification",
        reply: respond(200, { status: "success", predictions: [] }),
      },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    const labelsCard = cardOf(strings.analyzeLabelingCardTitle);
    expect(await within(labelsCard).findByText(strings.unreadableResponse)).toBeTruthy();
    expect(within(labelsCard).getAllByText(strings.receiptRaw).length).toBeGreaterThan(0);
  });

  it("keeps a drifted classifications payload on the warn-plus-raw path", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/labeling",
        reply: respond(200, { status: "success", labels: [] }),
      },
      {
        method: "GET",
        path: "/api/classification",
        reply: respond(200, { status: "success", predictions: [{ set_id: 1 }] }),
      },
    ]);
    render(<Analyze />, { wrapper: makeWrapper() });
    const classCard = cardOf(strings.analyzeClassificationReadTitle);
    expect(await within(classCard).findByText(strings.unreadableResponse)).toBeTruthy();
    expect(within(classCard).getAllByText(strings.receiptRaw).length).toBeGreaterThan(0);
  });
});
