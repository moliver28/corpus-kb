import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { statusOpFor } from "../api/ops";
import type { OpResult } from "../api/types";
import { strings } from "../strings";
import LongOpControls from "./LongOpControls";
import type { LongOp } from "../hooks/useLongOp";

/**
 * LongOpControls rows (spec T2): the one honest wait block. The
 * "Check status" button renders only when the op family has a status
 * op; a family without one gets "Run again" (when the card offers a
 * retry) and never a ledger button that knows nothing about it.
 */

const OK: OpResult = {
  ok: true, kind: "ok", httpStatus: 200, status: "ok",
  message: "", remediation: "", errorType: "", data: {},
};

const LEDGER: OpResult = {
  ok: true, kind: "ok", httpStatus: 200, status: "running",
  message: "still working", remediation: "", errorType: "", data: {},
};

function makeOp(overrides: Partial<LongOp>): LongOp {
  return {
    start: () => Promise.resolve(OK),
    cancel: () => {},
    checkStatus: () => Promise.resolve(OK),
    running: false,
    elapsedSec: 0,
    unknownOutcome: false,
    cancelled: false,
    result: null,
    statusResult: null,
    hasStatus: false,
    ...overrides,
  };
}

afterEach(cleanup);

describe("LongOpControls", () => {
  it("renders nothing while the op is idle", () => {
    const { container } = render(<LongOpControls op={makeOp({})} />);
    expect(container.textContent).toBe("");
  });

  it("shows the elapsed line and cancel button while running", () => {
    const cancel = vi.fn();
    render(<LongOpControls op={makeOp({ running: true, elapsedSec: 7, cancel })} />);
    expect(
      screen.getByText(strings.runnerRunningElapsed.replace("{sec}", "7")),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: strings.runnerCancelLabel }));
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: strings.runnerCheckStatus })).toBeNull();
  });

  it("uses the card's cancel handler when it records cancel intent", () => {
    const cancel = vi.fn();
    const onCancel = vi.fn();
    render(
      <LongOpControls op={makeOp({ running: true, cancel })} onCancel={onCancel} />,
    );
    fireEvent.click(screen.getByRole("button", { name: strings.runnerCancelLabel }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(cancel).not.toHaveBeenCalled();
  });

  it("offers Check status after cancel only when the family has a status op", () => {
    const checkStatus = vi.fn(() => Promise.resolve(LEDGER));
    render(<LongOpControls op={makeOp({ cancelled: true, hasStatus: true, checkStatus })} />);
    fireEvent.click(screen.getByRole("button", { name: strings.runnerCheckStatus }));
    expect(checkStatus).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: strings.runnerRunAgain })).toBeNull();
  });

  it("shows no Check status button for a family without a status op", () => {
    const onRetry = vi.fn();
    render(<LongOpControls op={makeOp({ cancelled: true })} onRetry={onRetry} />);
    expect(screen.queryByRole("button", { name: strings.runnerCheckStatus })).toBeNull();
    expect(screen.getByText(strings.runnerCancelled)).toBeTruthy();
  });

  it("offers Run again as the recovery when there is no status op", () => {
    const onRetry = vi.fn();
    render(
      <LongOpControls op={makeOp({ unknownOutcome: true })} onRetry={onRetry} />,
    );
    expect(screen.getByText(strings.runnerUnknownOutcome)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunAgain }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: strings.runnerCheckStatus })).toBeNull();
  });

  it("offers Check status on unknown outcome when the family has one", () => {
    const checkStatus = vi.fn(() => Promise.resolve(LEDGER));
    const onRetry = vi.fn();
    render(
      <LongOpControls
        op={makeOp({ unknownOutcome: true, hasStatus: true, checkStatus })}
        onRetry={onRetry}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: strings.runnerCheckStatus }));
    expect(checkStatus).toHaveBeenCalledTimes(1);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("renders the status line beside the receipt, never on it", () => {
    render(<LongOpControls op={makeOp({ statusResult: LEDGER })} />);
    expect(
      screen.getByText(strings.runnerStatusLine.replace("{message}", "still working")),
    ).toBeTruthy();
    const empty = { ...LEDGER, message: "" };
    cleanup();
    render(<LongOpControls op={makeOp({ statusResult: empty })} />);
    expect(screen.getByText(strings.runnerStatusEmpty)).toBeTruthy();
  });
});

describe("statusOpFor", () => {
  it("maps the ingest family to the ingest ledger and nobody else", () => {
    expect(statusOpFor("research-ingest")).toBe("ingest-status");
    expect(statusOpFor("ingest-directory")).toBe("ingest-status");
    expect(statusOpFor("ingest-url")).toBe("ingest-status");
    expect(statusOpFor("demo-load")).toBe("ingest-status");
    // Analysis, coding, ask, and the cycle have no status ledger row.
    expect(statusOpFor("analyze-run")).toBeNull();
    expect(statusOpFor("coding-run")).toBeNull();
    expect(statusOpFor("ask")).toBeNull();
    expect(statusOpFor("research-cycle")).toBeNull();
    expect(statusOpFor("no-such-op")).toBeNull();
  });
});
