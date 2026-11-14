import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { strings } from "../strings";
import StatusBanner from "./StatusBanner";

afterEach(cleanup);

/**
 * The banner's role follows its kind (WCAG 4.1.3): an error is urgent
 * and is announced at once (role alert); the rest stay polite updates
 * (role status). The kind word keeps color from being the only signal.
 */
describe("StatusBanner", () => {
  it("announces an error banner as an alert", () => {
    render(<StatusBanner kind="error">{strings.serverNotAnswering}</StatusBanner>);
    expect(screen.getByRole("alert").textContent).toContain(strings.serverNotAnswering);
    expect(screen.getByRole("alert").textContent).toContain(strings.bannerKindError);
  });

  it("keeps the other kinds on the polite status role", () => {
    render(
      <>
        <StatusBanner kind="info">{strings.scaleLedgerPaused}</StatusBanner>
        <StatusBanner kind="ok">{strings.reviewDecisionDone}</StatusBanner>
        <StatusBanner kind="warn">{strings.scaleLedgerBackoff}</StatusBanner>
      </>,
    );
    expect(screen.getAllByRole("status").length).toBe(3);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
