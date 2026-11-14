import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "./strings";

/**
 * The boot composition row (spec: the boundary wiring test): main.tsx
 * itself wraps the shell in the crash boundary, so a render error
 * anywhere under the router degrades to the fallback, never a white
 * screen. The shell is mocked to throw; everything around it is real.
 */
vi.mock("./App", () => ({
  default: () => {
    throw new Error("shell failed");
  },
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("main boot composition", () => {
  // The dynamic import pulls the whole app graph, which can take
  // seconds on a busy test worker; the default 5 s is not enough.
  it("wraps the shell in the app boundary", { timeout: 30_000 }, async () => {
    // main.tsx mounts into #root at import time. The stubbed fetch
    // rejects; loadRuntimeConfig's own catch falls back to the cached
    // defaults, exactly the absent-config path.
    document.body.innerHTML = '<div id="root"></div>';
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("no runtime config in tests"))),
    );
    const noise = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      await import("./main");
      // React re-renders the tree under the boundary after the throw,
      // so the fallback lands one task later.
      expect(await screen.findByText(strings.boundaryTitle)).toBeTruthy();
      expect(screen.getByText(strings.boundaryBody)).toBeTruthy();
      expect(
        await screen.findByRole("button", { name: strings.errorCopyDiagnostic }),
      ).toBeTruthy();
    } finally {
      noise.mockRestore();
    }
  });
});
