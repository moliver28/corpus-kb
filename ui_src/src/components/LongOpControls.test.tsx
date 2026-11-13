import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OPS, statusOpFor } from "../api/ops";
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
  // The full, explicit classification of every longRunning manifest op:
  // "ingest-status" only when the op's runs land in the ingest_runs
  // ledger (the standard corpus pipeline), null when no ledger tracks
  // them (research paths, analysis, coding, the cycle). A new longRunning
  // op fails this row until it is classified here AND mapped in
  // api/ops.ts — no silent wrong or missing "Check status" button.
  const LONG_OP_STATUS: Record<string, string | null> = {
    "analyze-run": null,
    "classification-build": null,
    // The issue #350 coding long ops (master): like the rest of the
    // coding family, they write no ingest_runs ledger row, so no
    // status poll exists for them.
    "coding-code-batch": null,
    "coding-embed-codebook": null,
    "coding-inductive": null,
    "coding-pool": null,
    "coding-run": null,
    "demo-load": "ingest-status",
    "ingest-directory": "ingest-status",
    "ingest-url": "ingest-status",
    "labeling-build": null,
    "research-cycle": null,
    "research-demo": null,
    // Research ingest writes no ingest_runs row (its events are the
    // ledger), so a status poll would only report unrelated runs.
    "research-ingest": null,
    "topics-build": null,
  };

  it("classifies every longRunning manifest op: status op or none", () => {
    const longOps = Object.keys(OPS)
      .filter((id) => OPS[id]?.longRunning === true)
      .sort();
    expect(longOps).toEqual(Object.keys(LONG_OP_STATUS).sort());
    for (const [id, expected] of Object.entries(LONG_OP_STATUS)) {
      expect(statusOpFor(id)).toBe(expected);
    }
  });

  it("never maps a short op or an unknown id", () => {
    expect(statusOpFor("ask")).toBeNull();
    expect(statusOpFor("research-ingest-transcript")).toBeNull();
    expect(statusOpFor("no-such-op")).toBeNull();
  });

  // The converse: every id the map answers for is longRunning, so a
  // stray short-op key in api/ops.ts fails this row.
  it("maps only longRunning ops to a status op", () => {
    const mapped = Object.keys(OPS).filter((id) => statusOpFor(id) !== null);
    expect(mapped.length).toBeGreaterThan(0);
    for (const id of mapped) {
      expect(OPS[id]?.longRunning).toBe(true);
    }
  });
});
