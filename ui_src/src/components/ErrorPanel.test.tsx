import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import type { OpResult } from "../api/types";
import ErrorPanel from "./ErrorPanel";

/**
 * The error panel's own rows: the blocked kind renders the structured
 * headline, and Copy diagnostic writes the whole blob and says so.
 */

const FAILED: OpResult = {
  ok: false,
  kind: "error",
  httpStatus: 500,
  status: "error",
  message: "The write failed.",
  remediation: "Check the server log.",
  errorType: "write_failed",
  data: { trace: "abc" },
};

afterEach(() => {
  cleanup();
  Object.defineProperty(navigator, "clipboard", {
    value: undefined,
    configurable: true,
  });
});

describe("ErrorPanel", () => {
  it("renders the plain headline with the message and the remediation", () => {
    render(<ErrorPanel result={FAILED} />);
    expect(screen.getByText(strings.errorTitle)).toBeTruthy();
    expect(screen.getByText("The write failed.")).toBeTruthy();
    expect(screen.getByText("Check the server log.")).toBeTruthy();
  });

  it("names a blocked refusal, not a generic error", () => {
    render(<ErrorPanel result={{ ...FAILED, kind: "blocked" }} />);
    expect(screen.getByText(strings.blockedTitle)).toBeTruthy();
    expect(screen.queryByText(strings.errorTitle)).toBeNull();
  });

  it("copies the diagnostic blob with the op id, then says Copied", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<ErrorPanel result={FAILED} opId="release-execute" />);
    fireEvent.click(screen.getByRole("button", { name: strings.errorCopyDiagnostic }));
    expect(await screen.findByText(strings.copiedNote)).toBeTruthy();
    expect(writeText).toHaveBeenCalledTimes(1);
    const blob = JSON.parse(writeText.mock.calls[0][0] as string);
    expect(blob.opId).toBe("release-execute");
    expect(blob.httpStatus).toBe(500);
    expect(blob.message).toBe("The write failed.");
    expect(blob.data).toEqual({ trace: "abc" });
  });
});
