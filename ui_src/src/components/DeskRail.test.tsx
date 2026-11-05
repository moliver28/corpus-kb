import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import DeskRail from "./DeskRail";
import type { RailStage } from "./DeskRail";
import { clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { MinimalResponse, Route } from "../test/testUtils";
import { strings } from "../strings";

/**
 * The Desk rail's live reads, through the shared fetch doubles: the
 * ingest dot's three states from the ingest-status poll, the corpus
 * label and the health rows from the doctor poll, and the waiting
 * fallback when the doctor poll fails. The dot's title attribute
 * carries "label — state word", so the hover hint is the assertion
 * surface (the glyph class rides along).
 */

// One ledger run, exactly as ingest-status serializes it (runFilter.ts).
const RUN = {
  run_id: "run-1",
  status: "completed",
  started_at: null,
  source_type: null,
  project_id: null,
};

/** A doctor result: one probe up, the other as told (the rows differ). */
function doctorReply(postgresOk: boolean, ollamaOk: boolean, profile: string): MinimalResponse {
  return respond(200, {
    status: "success",
    result: {
      profile: { profile, recommended_model: "nomic-embed-text" },
      postgres: { reachable: postgresOk, message: "ok" },
      ollama: { reachable: ollamaOk, message: "ok" },
      env: { name: "env", status: "ok", detail: "", fix: "", doc: "" },
      research_checks: [],
      storage_checks: [],
    },
  });
}

function ingestReply(body: Record<string, unknown>): MinimalResponse {
  return respond(200, { status: "success", ...body });
}

function railRoutes(doctor: MinimalResponse, ingest: MinimalResponse): Route[] {
  return [
    { method: "GET", path: "/api/doctor", reply: doctor },
    { method: "GET", path: "/api/ingest/status", reply: ingest },
  ];
}

// Two stages cover both dot lives: the live ingest read and a neutral one.
const STAGES: RailStage[] = [
  { key: "setup", label: strings.stageSetup },
  { key: "ingest", label: strings.stageIngest },
];

function renderRail(): void {
  render(<DeskRail stages={STAGES} selected="setup" onSelect={() => undefined} />, {
    wrapper: makeWrapper(false),
  });
}

/** The dot's hover hint, built the way DeskRail builds it. */
function dotTitle(label: string, word: string): string {
  return `${label} — ${word}`;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

describe("DeskRail", () => {
  it("marks the ingest dot active while runs are incomplete", async () => {
    stubFetch(
      railRoutes(doctorReply(true, true, "spearhead"), ingestReply({ incomplete_runs: 1, runs: [] })),
    );
    renderRail();
    const dot = await screen.findByTitle(dotTitle(strings.stageIngest, strings.railStateActive));
    expect(dot.className).toContain("ui-dot--active");
    // A stage without a live source stays pending — no invented state.
    const neutral = screen.getByTitle(dotTitle(strings.stageSetup, strings.railStatePending));
    expect(neutral.className).toContain("ui-dot--pending");
  });

  it("marks the ingest dot done when the ledger holds runs", async () => {
    stubFetch(railRoutes(doctorReply(true, true, "spearhead"), ingestReply({ runs: [RUN] })));
    renderRail();
    const dot = await screen.findByTitle(dotTitle(strings.stageIngest, strings.railStateDone));
    expect(dot.className).toContain("ui-dot--done");
  });

  it("keeps the ingest dot pending on an empty ledger", async () => {
    stubFetch(railRoutes(doctorReply(true, true, "spearhead"), ingestReply({ runs: [] })));
    renderRail();
    const dot = await screen.findByTitle(dotTitle(strings.stageIngest, strings.railStatePending));
    expect(dot.className).toContain("ui-dot--pending");
  });

  it("renders the corpus name and both health rows from the doctor answer", async () => {
    stubFetch(
      railRoutes(doctorReply(true, false, "spearhead"), ingestReply({ runs: [] })),
    );
    renderRail();
    expect(
      await screen.findByText(strings.railCorpusName.replace("{name}", "spearhead")),
    ).toBeTruthy();
    expect(await screen.findByText(`${strings.railHealthDb} ${strings.railHealthOk}`)).toBeTruthy();
    expect(screen.getByText(`${strings.railHealthModels} ${strings.railHealthDown}`)).toBeTruthy();
  });

  it("falls back to the waiting health strip when the doctor poll fails", async () => {
    // No routes: every fetch fails at once, so both polls land on the
    // honest fallback and no state is invented.
    stubFetch([]);
    renderRail();
    expect(await screen.findByText(strings.railHealthWaiting)).toBeTruthy();
    const dot = screen.getByTitle(dotTitle(strings.stageIngest, strings.railStatePending));
    expect(dot.className).toContain("ui-dot--pending");
  });
});
