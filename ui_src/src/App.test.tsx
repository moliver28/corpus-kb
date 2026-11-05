import { afterEach, describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import App from "./App";
import { strings } from "./strings";

// globals are off, so testing-library's auto-cleanup never registers.
afterEach(cleanup);

// The shell now mounts the real Setup screen, whose doctor card uses
// react-query — the same provider wrap main.tsx applies.
function renderApp() {
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

describe("App stage shell", () => {
  it("shows all eight lifecycle stages", () => {
    renderApp();
    const stageLabels = [
      strings.stageSetup,
      strings.stageIngest,
      strings.stageReview,
      strings.stageScale,
      strings.stageAsk,
      strings.stageAnalyze,
      strings.stageCodebook,
      strings.stageRelease,
    ];
    for (const label of stageLabels) {
      expect(screen.getByText(label)).toBeTruthy();
    }
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
});
