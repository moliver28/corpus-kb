import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ConfigProvider } from "antd";
import App from "./App";
import AppErrorBoundary from "./components/AppErrorBoundary";
import { stubFetch } from "./test/testUtils";
import { strings } from "./strings";
import { corpusKbTheme } from "./theme";

// globals are off, so testing-library's auto-cleanup never registers.
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** A card whose render always throws (the boundary test's crash). */
function ThrowingCard(): never {
  throw new Error("card failed");
}

// The shell now mounts the real Setup screen, whose doctor card uses
// react-query — the same provider wrap main.tsx applies, ConfigProvider
// included (provider parity: the tests render the boot composition).
// The shell's own polls (doctor, ingest-status) must not open real
// sockets, so every fetch here fails at once through the shared stub.
function renderApp() {
  stubFetch([]);
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <ConfigProvider theme={corpusKbTheme}>
        <App />
      </ConfigProvider>
    </QueryClientProvider>,
  );
}

describe("App stage shell", () => {
  it("shows all eight lifecycle stages and the graph domain view", () => {
    renderApp();
    const stageLabels = [
      strings.stageSetup,
      strings.stageIngest,
      strings.stageReview,
      strings.stageScale,
      strings.stageAsk,
      strings.stageAnalyze,
      strings.stageGraph,
      strings.stageCodebook,
      strings.stageRelease,
    ];
    for (const label of stageLabels) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it("renders the app title as the page's h1", () => {
    renderApp();
    expect(
      screen.getByRole("heading", { level: 1, name: strings.appTitle }),
    ).toBeTruthy();
  });

  it("keeps the corpus switcher an honest not-wired row in the rail", () => {
    renderApp();
    expect(screen.getByText(strings.railSwitcherNotWired)).toBeTruthy();
  });

  it("shows the rail health strip as not checked until the doctor answers", async () => {
    renderApp();
    expect(await screen.findByText(strings.railHealthWaiting)).toBeTruthy();
  });

  it("lands on the Setup stage", async () => {
    renderApp();
    // The stage screens are lazy chunks; the stage title appears after
    // the chunk resolves. Under full-suite worker load that resolve can
    // outwait the library's 1 s default, so the wait runs wide (15 s).
    expect(
      await screen.findByText(strings.setupTitle, {}, { timeout: 15_000 }),
    ).toBeTruthy();
  });

  it("degrades a throwing card to the boundary fallback, not a white screen", () => {
    // React logs every caught render error; the assertion is the
    // fallback UI, so the noise stays out of the gate output.
    const noise = vi.spyOn(console, "error").mockImplementation(() => undefined);
    try {
      render(
        <AppErrorBoundary>
          <ThrowingCard />
        </AppErrorBoundary>,
      );
      expect(screen.getByText(strings.boundaryTitle)).toBeTruthy();
      expect(screen.getByText(strings.boundaryBody)).toBeTruthy();
      expect(
        screen.getByRole("button", { name: strings.errorCopyDiagnostic }),
      ).toBeTruthy();
      // Retry re-mounts the tree; the deterministic throw lands in the
      // same fallback instead of crashing the test run.
      fireEvent.click(screen.getByRole("button", { name: strings.retryLabel }));
      expect(screen.getByText(strings.boundaryTitle)).toBeTruthy();
    } finally {
      noise.mockRestore();
    }
  });
});
