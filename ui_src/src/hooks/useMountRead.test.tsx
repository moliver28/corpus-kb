import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useMountRead } from "./useMountRead";

/**
 * The one mount-read story: the read fires once on mount, a re-render
 * with a fresh closure never re-fires it (the copy-paste trap the ten
 * cards guarded against in comments), and a bigger `bump` number fires
 * the latest closure again.
 */

function PlainProbe({ read }: { read: () => void }) {
  useMountRead(read);
  return <span>plain</span>;
}

function BumpProbe({ read, bump }: { read: () => void; bump: number }) {
  useMountRead(read, bump);
  return <span>bump</span>;
}

afterEach(() => {
  cleanup();
});

describe("useMountRead", () => {
  it("fires the read once on mount and never again on re-renders", () => {
    const first = vi.fn();
    const view = render(<PlainProbe read={first} />);
    expect(first).toHaveBeenCalledTimes(1);
    // A new closure identity every render (an inline arrow's story) must
    // not fire the read again.
    const second = vi.fn();
    act(() => {
      view.rerender(<PlainProbe read={second} />);
    });
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).not.toHaveBeenCalled();
  });

  it("fires the latest closure again when the bump number changes", () => {
    const first = vi.fn();
    const view = render(<BumpProbe read={first} bump={0} />);
    expect(first).toHaveBeenCalledTimes(1);
    const second = vi.fn();
    act(() => {
      view.rerender(<BumpProbe read={second} bump={1} />);
    });
    // The new bump fires the new closure; the old one stays at one call.
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenCalledTimes(1);
    // The same bump number again fires nothing.
    const third = vi.fn();
    act(() => {
      view.rerender(<BumpProbe read={third} bump={1} />);
    });
    expect(third).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("returns nothing and lets the caller keep its own state shaping", () => {
    // The hook's contract: a void call beside the caller's own useOp
    // state — no result, no running flag, no reset to clash with it.
    const read = vi.fn();
    const view = render(<PlainProbe read={read} />);
    expect(view.getByText("plain")).toBeTruthy();
  });
});
