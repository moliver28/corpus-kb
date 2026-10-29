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

  it("renders the validation summary when the receipt carries one", () => {
    render(
      <ReceiptCard
        result={resultOf("success", {
          validation: { mode: "advisory", status: "findings", problem_count: 2 },
        })}
      />,
    );
    const banners = screen.getAllByRole("status");
    expect(
      banners.some((b) =>
        (b.textContent ?? "").includes(strings.receiptValidationLabel),
      ),
    ).toBe(true);
    expect(
      screen.getByText(strings.receiptValidationFindings.replace("{n}", "2")),
    ).toBeTruthy();
  });

  it("states a clean validation pass in plain words", () => {
    render(
      <ReceiptCard
        result={resultOf("success", {
          validation: { mode: "advisory", status: "ok", problem_count: 0 },
        })}
      />,
    );
    expect(screen.getByText(strings.receiptValidationOk)).toBeTruthy();
  });

  it("counts the near-duplicate drops only when the field is present", () => {
    render(
      <ReceiptCard
        result={resultOf("success", { dedup_dropped: ["c1", "c2", "c3"] })}
      />,
    );
    expect(
      screen.getByText(strings.receiptDedupDropped.replace("{n}", "3")),
    ).toBeTruthy();
  });

  it("renders neither ingestion fact when the receipt lacks them", () => {
    render(<ReceiptCard result={resultOf("success")} />);
    expect(screen.queryByText(strings.receiptValidationLabel)).toBeNull();
    expect(
      screen.queryByText(strings.receiptDedupDropped.replace("{n}", "0")),
    ).toBeNull();
  });
});
