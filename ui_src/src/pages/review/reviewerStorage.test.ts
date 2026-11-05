import { afterEach, describe, expect, it } from "vitest";
import { loadReviewer, saveReviewer } from "./reviewerStorage";

/**
 * The reviewer storage contract: the versioned key round-trips, and the
 * pre-version key carries over once instead of being silently dropped.
 */

afterEach(() => {
  window.localStorage.clear();
});

describe("reviewerStorage", () => {
  it("round-trips the name under the versioned key", () => {
    saveReviewer("Alex");
    expect(loadReviewer()).toBe("Alex");
    expect(window.localStorage.getItem("corpus-kb:reviewer:v1")).toBe("Alex");
  });

  it("carries a pre-version name over and retires the old key", () => {
    window.localStorage.setItem("corpus-kb:reviewer", "Rowan");
    expect(loadReviewer()).toBe("Rowan");
    expect(window.localStorage.getItem("corpus-kb:reviewer:v1")).toBe("Rowan");
    expect(window.localStorage.getItem("corpus-kb:reviewer")).toBeNull();
  });

  it("keeps the versioned name when both keys exist", () => {
    window.localStorage.setItem("corpus-kb:reviewer", "Old");
    window.localStorage.setItem("corpus-kb:reviewer:v1", "New");
    expect(loadReviewer()).toBe("New");
    expect(window.localStorage.getItem("corpus-kb:reviewer")).toBeNull();
  });

  it("reads an empty store as no name", () => {
    expect(loadReviewer()).toBe("");
  });
});
