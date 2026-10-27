import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { strings } from "../strings";
import ConfirmGate from "./ConfirmGate";

/**
 * The gate must not be fireable from the keyboard without a click on
 * purpose: opening the dialog never focuses the OK button, so a stray
 * Enter cannot confirm a long or destructive op.
 */

afterEach(cleanup);

describe("ConfirmGate", () => {
  it("opens without focusing the OK button, so Enter cannot fire it", async () => {
    render(<ConfirmGate title="Run this?" description="It takes a minute." onConfirm={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    const ok = await screen.findByRole("button", { name: strings.confirmConfirm });
    expect(ok).toBeTruthy();
    expect(document.activeElement).not.toBe(ok);
  });
});
