import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { useCopy } from "./useCopy";

/**
 * The shared copy hook (shared seam): the note resets after two
 * seconds, a missing clipboard says so instead of staying silent, and
 * the timer never fires after unmount.
 */

/** One button wired to the hook, with the key passed through. */
function CopyButton({ text, keyed }: { text: string; keyed?: string }) {
  const { copiedKey, unavailable, copy } = useCopy();
  const copied = copiedKey !== null && (keyed === undefined || copiedKey === keyed);
  return (
    <button type="button" onClick={() => copy(text, keyed)}>
      {unavailable
        ? strings.copyUnavailable
        : copied
          ? strings.copiedNote
          : strings.errorCopyDiagnostic}
    </button>
  );
}

function stubClipboard(writeText: (text: string) => Promise<void>): void {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  Object.defineProperty(navigator, "clipboard", {
    value: undefined,
    configurable: true,
  });
});

describe("useCopy", () => {
  it("shows the copied note, then resets it after two seconds", async () => {
    vi.useFakeTimers();
    stubClipboard(vi.fn().mockResolvedValue(undefined));
    render(<CopyButton text="yaml-body" />);
    const button = screen.getByRole("button");
    expect(button.textContent).toBe(strings.errorCopyDiagnostic);
    fireEvent.click(button);
    await act(async () => {});
    expect(button.textContent).toBe(strings.copiedNote);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(button.textContent).toBe(strings.errorCopyDiagnostic);
  });

  it("says the copy is unavailable when the browser hides the clipboard", async () => {
    vi.useFakeTimers();
    render(<CopyButton text="yaml-body" />);
    const button = screen.getByRole("button");
    fireEvent.click(button);
    await act(async () => {});
    expect(button.textContent).toBe(strings.copyUnavailable);
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(button.textContent).toBe(strings.errorCopyDiagnostic);
  });

  it("keeps the note keyed, so only the copied row reads as copied", async () => {
    vi.useFakeTimers();
    stubClipboard(vi.fn().mockResolvedValue(undefined));
    render(<CopyButton text="row-one" keyed="row-one" />);
    const button = screen.getByRole("button");
    fireEvent.click(button);
    await act(async () => {});
    expect(button.textContent).toBe(strings.copiedNote);
  });

  it("clears the reset timer on unmount", async () => {
    vi.useFakeTimers();
    stubClipboard(vi.fn().mockResolvedValue(undefined));
    const clearSpy = vi.spyOn(window, "clearTimeout");
    const { unmount } = render(<CopyButton text="yaml-body" />);
    fireEvent.click(screen.getByRole("button"));
    await act(async () => {});
    unmount();
    // The pending reset never outlives the tree that set it.
    expect(clearSpy).toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(5000);
    });
  });
});
