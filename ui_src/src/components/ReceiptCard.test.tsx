import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { OpResult } from "../api/types";
import { strings } from "../strings";
import releaseReviewOk from "../test/fixtures/release-review-ok-200.json";
import notebookEvidenceOk from "../test/fixtures/notebook-evidence-ok-200.json";
import ReceiptCard from "./ReceiptCard";

afterEach(cleanup);

/**
 * The receipt headline is a honesty surface: the closed neutral family
 * (status-less, empty, degraded, partial, abstained, disabled, skipped)
 * carries its own headline, and the open completion family rides the
 * shared normalizer's ok — never a hand word-list.
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

/** One OpResult over a canonical fixture body (live-shape proof). */
function resultFromBody(body: Record<string, unknown>): OpResult {
  return {
    ok: true,
    kind: "ok",
    httpStatus: 200,
    status: typeof body.status === "string" ? body.status : "",
    message: "",
    remediation: "",
    errorType: "",
    data: body,
  };
}

describe("ReceiptCard", () => {
  it("claims Done only for a success-status envelope", () => {
    render(<ReceiptCard result={resultOf("success")} />);
    expect(screen.getByText(strings.receiptDone)).toBeTruthy();
  });

  it("accepts the server's ok word as the success family", () => {
    render(<ReceiptCard result={resultOf("ok")} />);
    expect(screen.getByText(strings.receiptDone)).toBeTruthy();
  });

  // The completion family is open (each release and notebook flow coins
  // its own word), so the receipt calls the normalizer, not a list.
  it("keeps the release review packet on the Done headline", () => {
    render(<ReceiptCard result={resultFromBody(releaseReviewOk.response.body)} />);
    expect(screen.getByText(strings.receiptDone)).toBeTruthy();
    expect(screen.queryByText(strings.receiptNotSuccess)).toBeNull();
  });

  it("keeps the release request, compare, and execute words on Done", () => {
    // Server words: release/service.py:123,201 and release_handlers.py:226.
    for (const status of ["requested", "compared", "released"]) {
      render(<ReceiptCard result={resultOf(status)} />);
      expect(screen.getByText(strings.receiptDone)).toBeTruthy();
      expect(screen.queryByText(strings.receiptNotSuccess)).toBeNull();
      cleanup();
    }
  });

  it("keeps the notebook evidence flow words on Done", () => {
    render(<ReceiptCard result={resultFromBody(notebookEvidenceOk.response.body)} />);
    expect(screen.getByText(strings.receiptDone)).toBeTruthy();
    // research/notebook.py:172 answers this word on the ask path.
    render(<ReceiptCard result={resultOf("answer")} />);
    expect(screen.getAllByText(strings.receiptDone).length).toBe(2);
    expect(screen.queryByText(strings.receiptNotSuccess)).toBeNull();
  });

  it("classifies an unknown word by the normalizer's ok, one point", () => {
    // ok (what api/errors.ts answers for a word it does not know) -> Done.
    render(<ReceiptCard result={resultOf("celebrated")} />);
    expect(screen.getByText(strings.receiptDone)).toBeTruthy();
  });

  it("reads a not-ok result as not-success with the reason banner", () => {
    render(<ReceiptCard result={{ ...resultOf("celebrated"), ok: false }} />);
    expect(screen.getAllByText(strings.receiptNotSuccess).length).toBeGreaterThan(0);
    expect(screen.queryByText(strings.receiptDone)).toBeNull();
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
