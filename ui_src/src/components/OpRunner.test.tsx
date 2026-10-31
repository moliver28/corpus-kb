import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { makeWrapper, stubFetchImpl } from "../test/testUtils";
import type { MinimalResponse } from "../test/testUtils";
import OpRunner from "./OpRunner";

/**
 * OpRunner rows: an id outside the manifest renders the honest unknown-op
 * panel, and the retry of a settled longRunning op goes back through the
 * LONG path (elapsed banner, no client wait cap) — never the short-wait
 * simple path.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("OpRunner", () => {
  it("renders the unknown-op error panel for an id outside the manifest", () => {
    render(<OpRunner opId="no-such-op" />, { wrapper: makeWrapper(false) });
    expect(screen.getByText(strings.unknownOperation)).toBeTruthy();
  });

  it("retries a settled longRunning op through the long path", async () => {
    let calls = 0;
    stubFetchImpl(() => {
      calls += 1;
      if (calls === 1) {
        return Promise.resolve({
          status: 500,
          text: async () => JSON.stringify({ status: "error", error: "boom" }),
        });
      }
      return new Promise<MinimalResponse>(() => {}); // a long op keeps waiting
    });
    render(<OpRunner opId="research-demo" />, { wrapper: makeWrapper(false) });

    // The initial run goes through the confirm gate, then fails.
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    await screen.findByText(strings.errorTitle);

    // The retry must re-enter the long path, not the timeout-bounded one.
    fireEvent.click(screen.getByRole("button", { name: strings.retryLabel }));
    expect(await screen.findByText(/Running for \d+ seconds\./)).toBeTruthy();
    expect(screen.getByRole("button", { name: strings.runnerCancelLabel })).toBeTruthy();
  });
});
