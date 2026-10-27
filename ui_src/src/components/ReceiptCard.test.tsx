import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { OpResult } from "../api/types";
import { strings } from "../strings";
import ReceiptCard from "./ReceiptCard";

afterEach(cleanup);

/**
 * The receipt headline is a honesty surface: only a success (or a
 * known neutral word like partial/empty) may read as done. The
 * disabled/skipped envelopes render the server's reason and remedy.
 */

function resultOf(status: string, extra: Record<string, unknown> = {}): OpResult {
  return {
    ok: true,
    kind: "ok",
    httpStatus: 200,
    status,
    message: "",
    remediation: "",
    errorType: "",
    data: { status, ...extra },
  };
}

describe("ReceiptCard", () => {
  it("claims Done only for a success-status envelope", () => {
    render(<ReceiptCard result={resultOf("success")} />);
    expect(screen.getByText(strings.receiptDone)).toBeTruthy();
  });

  it("never claims Done for disabled; it shows reason and remedy", () => {
    render(
      <ReceiptCard
        result={resultOf("disabled", {
          reason: "topics.enabled is false in the loaded config.",
          remedy: "Set topics.enabled: true, then retry.",
        })}
      />,
    );
    expect(screen.queryByText(strings.receiptDone)).toBeNull();
    expect(screen.getByText(strings.receiptNotSuccess)).toBeTruthy();
    expect(
      screen.getByText("topics.enabled is false in the loaded config."),
    ).toBeTruthy();
    expect(screen.getByText("Set topics.enabled: true, then retry.")).toBeTruthy();
  });

  it("renders skipped with the remediation fallback from the result", () => {
    render(
      <ReceiptCard
        result={{
          ...resultOf("skipped"),
          remediation: "Re-run with force: true.",
        }}
      />,
    );
    expect(screen.queryByText(strings.receiptDone)).toBeNull();
    expect(screen.getByText("Re-run with force: true.")).toBeTruthy();
  });

  it("keeps the Done headline off a status-less artifact", () => {
    render(<ReceiptCard result={{ ...resultOf("success"), status: "" }} />);
    expect(screen.getByText(strings.receiptEmpty)).toBeTruthy();
    expect(screen.queryByText(strings.receiptDone)).toBeNull();
  });
});
