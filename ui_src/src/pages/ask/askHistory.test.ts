import { afterEach, describe, expect, it } from "vitest";
import { loadHistory, saveHistory, type HistoryEntry } from "./askHistory";

/**
 * The client-only history's storage contract: a corrupt or hostile
 * sessionStorage payload reads as empty (never throws, never crashes
 * the card), and save caps the list at ten entries.
 */

afterEach(() => {
  window.sessionStorage.clear();
});

describe("askHistory", () => {
  it("reads an empty store as no entries", () => {
    expect(loadHistory()).toEqual([]);
  });

  it("round-trips saved entries, newest first", () => {
    const entries: HistoryEntry[] = [
      { question: "first", at: "2026-01-01T00:00:00Z" },
      { question: "second", at: "2026-01-02T00:00:00Z" },
    ];
    saveHistory(entries);
    expect(loadHistory()).toEqual(entries);
    expect(window.sessionStorage.getItem("corpus-kb:ask-history")).toContain("second");
  });

  it("reads corrupt JSON as empty", () => {
    window.sessionStorage.setItem("corpus-kb:ask-history", "{not json");
    expect(loadHistory()).toEqual([]);
  });

  it("reads a non-array payload as empty", () => {
    window.sessionStorage.setItem(
      "corpus-kb:ask-history",
      JSON.stringify({ question: "x", at: "y" }),
    );
    expect(loadHistory()).toEqual([]);
  });

  it("drops entries that miss their fields instead of crashing the card", () => {
    window.sessionStorage.setItem(
      "corpus-kb:ask-history",
      JSON.stringify([
        { question: "kept", at: "2026-01-01T00:00:00Z" },
        { question: 42, at: "2026-01-01T00:00:00Z" },
        "junk",
        null,
        { question: "no-time" },
      ]),
    );
    expect(loadHistory()).toEqual([{ question: "kept", at: "2026-01-01T00:00:00Z" }]);
  });

  it("caps the saved list at ten entries, in the caller's order", () => {
    // The slice keeps the caller's first ten; the card saves newest-first.
    const many: HistoryEntry[] = Array.from({ length: 14 }, (_, i) => ({
      question: `q-${String(i + 1).padStart(2, "0")}`,
      at: `2026-01-01T00:00:${String(i).padStart(2, "0")}Z`,
    }));
    saveHistory(many);
    const loaded = loadHistory();
    expect(loaded.length).toBe(10);
    expect(loaded[0].question).toBe("q-01");
    expect(loaded[9].question).toBe("q-10");
  });
});
