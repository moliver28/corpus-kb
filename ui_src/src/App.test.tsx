import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import App from "./App";
import { strings } from "./strings";

// globals are off, so testing-library's auto-cleanup never registers.
afterEach(cleanup);

describe("App stage shell", () => {
  it("shows all eight lifecycle stages", () => {
    render(<App />);
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

  it("lands on the Setup stage", () => {
    render(<App />);
    expect(screen.getByText(strings.setupTitle)).toBeTruthy();
  });
});
