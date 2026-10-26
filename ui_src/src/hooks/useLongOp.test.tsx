import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import type { ReactNode } from "react";
import type { OpResult } from "../api/types";
import { networkResult } from "../api/errors";
import { useLongOp } from "./useLongOp";

/**
 * useLongOp rows (spec D.5): double-start refused (same promise),
 * beforeunload guard while running, cancel -> unknown-outcome, network
 * result -> unknown-outcome, elapsed timer ticks each second, and a
 * status poll never clobbers the settled receipt.
 */

function makeWrapper() {
  const client = new QueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function deferred(): { promise: Promise<OpResult>; resolve: (r: OpResult) => void } {
  let resolve!: (r: OpResult) => void;
  const promise = new Promise<OpResult>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const OK: OpResult = {
  ok: true, kind: "ok", httpStatus: 200, status: "ok",
  message: "", remediation: "", errorType: "", data: {},
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useLongOp", () => {
  it("refuses a second start while running and returns the same promise", async () => {
    const first = deferred();
    const fn: Mock<(signal: AbortSignal) => Promise<OpResult>> = vi.fn(() => first.promise);
    const { result } = renderHook(() => useLongOp("op", fn), { wrapper: makeWrapper() });

    let flight1!: Promise<OpResult>;
    let flight2!: Promise<OpResult>;
    act(() => {
      flight1 = result.current.start();
    });
    // react-query schedules the mutationFn on a microtask.
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      flight2 = result.current.start();
    });
    expect(fn).toHaveBeenCalledTimes(1);
    expect(flight2).toBe(flight1);
    expect(result.current.running).toBe(true);

    await act(async () => {
      first.resolve(OK);
      await flight1;
    });
    expect(result.current.running).toBe(false);
  });

  it("registers and releases the beforeunload guard around the flight", async () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const flightData = deferred();
    const { result } = renderHook(
      () => useLongOp("op", () => flightData.promise),
      { wrapper: makeWrapper() },
    );

    let flight!: Promise<OpResult>;
    act(() => {
      flight = result.current.start();
    });
    expect(addSpy.mock.calls.some(([name]) => name === "beforeunload")).toBe(true);

    await act(async () => {
      flightData.resolve(OK);
      await flight;
    });
    expect(removeSpy.mock.calls.some(([name]) => name === "beforeunload")).toBe(true);
    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it("marks unknown-outcome when the network kind comes back", async () => {
    const { result } = renderHook(() => useLongOp("op", () => Promise.resolve(networkResult())), {
      wrapper: makeWrapper(),
    });
    await act(async () => {
      await result.current.start();
    });
    expect(result.current.unknownOutcome).toBe(true);
    expect(result.current.result?.kind).toBe("network");
  });

  it("cancel stops the wait and lands in unknown-outcome", async () => {
    const { result } = renderHook(
      () =>
        useLongOp("op", (signal) => new Promise<OpResult>((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new Error("aborted")));
        })),
      { wrapper: makeWrapper() },
    );
    let flight!: Promise<OpResult>;
    act(() => {
      flight = result.current.start();
    });
    await act(async () => {
      await Promise.resolve();
    });
    act(() => {
      result.current.cancel();
    });
    await act(async () => {
      await flight;
    });
    expect(result.current.unknownOutcome).toBe(true);
    expect(result.current.running).toBe(false);
  });

  it("keeps the receipt when a status poll returns the ledger shape", async () => {
    vi.stubGlobal(
      "fetch",
      (() =>
        Promise.resolve({
          status: 200,
          text: async () => JSON.stringify({ status: "running", message: "still working" }),
        })) as unknown as typeof fetch,
    );
    const receipt: OpResult = { ...OK, message: "the receipt" };
    const flightData = deferred();
    const { result } = renderHook(() => useLongOp("op", () => flightData.promise), {
      wrapper: makeWrapper(),
    });
    let flight!: Promise<OpResult>;
    await act(async () => {
      flight = result.current.start();
      flightData.resolve(receipt);
      await flight;
    });
    expect(result.current.result?.message).toBe("the receipt");

    await act(async () => {
      await result.current.checkStatus();
    });
    // The ledger answer lands in statusResult — the receipt is intact.
    expect(result.current.result?.message).toBe("the receipt");
    expect(result.current.statusResult?.status).toBe("running");
    expect(result.current.statusResult?.message).toBe("still working");
  });

  it("ticks elapsedSec once per second while running", async () => {
    vi.useFakeTimers();
    const flightData = deferred();
    const { result } = renderHook(() => useLongOp("op", () => flightData.promise), {
      wrapper: makeWrapper(),
    });
    act(() => {
      result.current.start();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(result.current.elapsedSec).toBe(1);
    await act(async () => {
      flightData.resolve(OK);
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.running).toBe(false);
  });
});
