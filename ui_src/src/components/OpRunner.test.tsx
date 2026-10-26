import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { strings } from "../strings";
import OpRunner from "./OpRunner";

/**
 * OpRunner rows: an id outside the manifest renders the honest unknown-op
 * panel, and the retry of a settled longRunning op goes back through the
 * LONG path (elapsed banner, no client wait cap) — never the short-wait
 * simple path.
 */

function makeWrapper() {
  const client = new QueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

type MinimalResponse = { status: number; text: () => Promise<string> };

function stubFetch(impl: () => Promise<MinimalResponse>): void {
  vi.stubGlobal("fetch", impl as unknown as typeof fetch);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("OpRunner", () => {
  it("renders the unknown-op error panel for an id outside the manifest", () => {
    render(<OpRunner opId="no-such-op" />, { wrapper: makeWrapper() });
    expect(screen.getByText(strings.unknownOperation)).toBeTruthy();
  });

  it("retries a settled longRunning op through the long path", async () => {
    let calls = 0;
    stubFetch(() => {
      calls += 1;
      if (calls === 1) {
        return Promise.resolve({
          status: 500,
          text: async () => JSON.stringify({ status: "error", error: "boom" }),
        });
      }
      return new Promise<MinimalResponse>(() => {}); // a long op keeps waiting
    });
    render(<OpRunner opId="research-demo" />, { wrapper: makeWrapper() });

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
