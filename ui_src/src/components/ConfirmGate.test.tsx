import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import ConfirmGate from "./ConfirmGate";

/**
 * The gate must not be fireable from the keyboard without a click on
 * purpose: opening the dialog never focuses the OK button, so a stray
 * Enter cannot confirm a long or destructive op. And one open cycle
 * confirms once: a second OK while the request is in flight is refused.
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

  it("refuses a second OK while the confirm request is still in flight", async () => {
    // A caller that keeps the dialog open while the request runs must
    // not see a second onConfirm from another OK click.
    const onConfirm = vi.fn();
    const gate = (open: boolean): ReactElement => (
      <ConfirmGate title="Run this?" description="It takes a minute." open={open} onConfirm={onConfirm} />
    );
    const { rerender } = render(gate(true));
    const ok = await screen.findByRole("button", { name: strings.confirmConfirm });
    fireEvent.click(ok);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    fireEvent.click(ok);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    // The guard resets when the gate closes and opens again.
    rerender(gate(false));
    rerender(gate(true));
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmConfirm }));
    expect(onConfirm).toHaveBeenCalledTimes(2);
  });
});
