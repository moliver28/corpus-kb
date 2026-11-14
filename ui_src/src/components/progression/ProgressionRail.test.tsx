import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { METHODS, type MethodGuidance } from "../../api/methods.generated";
import { progression } from "../../strings/progression";
import ProgressionRail from "./ProgressionRail";

// The rail is pure data: these tests render it from a fixture table
// (no fetch, no server) and once against the real generated register
// table. Panel content is lazy in antd Collapse, so each check expands
// its rung first.

const FIXTURE: MethodGuidance[] = [
  {
    id: "01",
    name: "Read and count",
    summary: "Counts labeled examples by hand.",
    op: null,
    difficulty: "novice",
    concepts: ["word_counting"],
    prerequisites: [],
    next: ["02"],
  },
  {
    id: "02",
    name: "Scan the words",
    summary: "Ranks frequent words.",
    op: "scan-run",
    difficulty: "novice",
    concepts: [],
    prerequisites: ["01"],
    next: [],
  },
  {
    id: "03",
    name: "Hold the map",
    summary: "Waits for the register to name it.",
    op: "hold-run",
    difficulty: null,
    concepts: [],
    prerequisites: [],
    next: [],
  },
];

function railOf(title: string): HTMLElement {
  const card = screen.getByText(title).closest(".ant-card");
  if (card === null) {
    throw new Error(`no card found for ${title}`);
  }
  return card as HTMLElement;
}

afterEach(cleanup);

describe("ProgressionRail", () => {
  it("groups fixture methods into rungs in fixed order and opens on demand", async () => {
    render(<ProgressionRail methods={FIXTURE} />);
    const rail = railOf(progression.progressionTitle);
    // Fixed rung order with per-rung counts; the untagged bucket renders last.
    expect(within(rail).getByText(progression.progressionRungNovice)).toBeTruthy();
    expect(within(rail).getByText(progression.progressionRungIntermediate)).toBeTruthy();
    expect(within(rail).getByText(progression.progressionRungAdvanced)).toBeTruthy();
    expect(within(rail).getByText(progression.progressionRungUntagged)).toBeTruthy();
    expect(within(rail).getByText("2 methods")).toBeTruthy();
    // Collapsed by default: method names stay out of the document.
    expect(within(rail).queryByText("Read and count")).toBeNull();
    // An empty rung says so honestly.
    fireEvent.click(within(rail).getByText(progression.progressionRungIntermediate));
    expect(
      await within(rail).findByText(progression.progressionRungEmpty, {}, { timeout: 15_000 }),
    ).toBeTruthy();
    // A rung opens: the method rows carry the register's guidance.
    fireEvent.click(within(rail).getByText(progression.progressionRungNovice));
    expect(await within(rail).findByText("Read and count", {}, { timeout: 15_000 })).toBeTruthy();
    expect(within(rail).getByText("Teaches: word counting")).toBeTruthy();
    expect(within(rail).getByText("Builds toward: Scan the words (02)")).toBeTruthy();
    expect(within(rail).getByText("Needs first: Read and count (01)")).toBeTruthy();
    // The human-led row says so; links to unknown ids stay bare.
    expect(within(rail).getByText(progression.progressionHumanOnly)).toBeTruthy();
    fireEvent.click(within(rail).getByText(progression.progressionRungUntagged));
    // Opened: the untagged label shows on the rung header AND the row's
    // difficulty tag — the count proves both render.
    expect(
      within(rail).getAllByText(progression.progressionRungUntagged).length,
    ).toBeGreaterThanOrEqual(2);
  });

  it("renders the full generated register table with every method reachable", async () => {
    render(<ProgressionRail />);
    const rail = railOf(progression.progressionTitle);
    for (const rung of [
      progression.progressionRungNovice,
      progression.progressionRungIntermediate,
      progression.progressionRungAdvanced,
    ]) {
      fireEvent.click(within(rail).getByText(rung));
    }
    // Every generated method id renders exactly once across the rungs.
    await within(rail).findByText(METHODS[0].name, {}, { timeout: 15_000 });
    const ids = within(rail).getAllByText(/^(0[1-9]|[12][0-9]|30)$/);
    expect(ids.length).toBe(METHODS.length);
    expect(METHODS.length).toBe(30);
  });
});
