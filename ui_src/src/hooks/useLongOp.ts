// Long-running op hook (spec D.5): one flight at a time, elapsed timer,
// unload guard, and an honest unknown-outcome state after a network drop.
import { useMutation } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { callOp } from "../api/client";
import { networkResult, unknownOpResult } from "../api/errors";
import { getOp } from "../api/ops";
import type { OpResult } from "../api/types";

export interface LongOpState {
  running: boolean;
  elapsedSec: number;
  /** The wait ended without an answer (network drop). */
  unknownOutcome: boolean;
  /** The USER stopped waiting; the server may still be working. */
  cancelled: boolean;
  result: OpResult<unknown> | null;
  /** The last status-poll ledger answer, kept beside the receipt. */
  statusResult: OpResult<unknown> | null;
}

export interface LongOp {
  start: () => Promise<OpResult<unknown>>;
  /** Cancel stops WAITING, not the server. */
  cancel: () => void;
  /** Poll the ingest ledger once (the "Check status" button). */
  checkStatus: () => Promise<OpResult<unknown>>;
  running: boolean;
  elapsedSec: number;
  unknownOutcome: boolean;
  cancelled: boolean;
  result: OpResult<unknown> | null;
  statusResult: OpResult<unknown> | null;
}

function warnBeforeUnload(event: BeforeUnloadEvent): void {
  event.preventDefault();
  event.returnValue = "";
}

export function useLongOp(
  key: string,
  fn: (signal: AbortSignal) => Promise<OpResult<unknown>>,
): LongOp {
  const [state, setState] = useState<LongOpState>({
    running: false,
    elapsedSec: 0,
    unknownOutcome: false,
    cancelled: false,
    result: null,
    statusResult: null,
  });
  const flightRef = useRef<Promise<OpResult<unknown>> | null>(null);
  const ctrlRef = useRef<AbortController | null>(null);
  const timerRef = useRef<number | null>(null);

  const stopWait = useCallback((): void => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    window.removeEventListener("beforeunload", warnBeforeUnload);
  }, []);

  // Unmount safety: no timer or unload guard survives the hook.
  useEffect(() => stopWait, [stopWait]);

  const settle = useCallback(
    (res: OpResult<unknown>): OpResult<unknown> => {
      // An aborted signal means the USER stopped waiting: the answer
      // must never surface as a server claim (error, receipt, or
      // unknown-outcome). The receipt slot stays empty instead.
      const cancelled = ctrlRef.current?.signal.aborted === true;
      stopWait();
      ctrlRef.current = null;
      flightRef.current = null;
      setState((prev) =>
        cancelled
          ? {
              ...prev,
              running: false,
              unknownOutcome: false,
              cancelled: true,
              result: null,
            }
          : {
              ...prev,
              running: false,
              unknownOutcome: res.kind === "network",
              cancelled: false,
              result: res,
            },
      );
      return res;
    },
    [stopWait],
  );

  const mutation = useMutation({
    mutationKey: ["long-op", key],
    mutationFn: (signal: AbortSignal) => fn(signal),
  });

  const start = useCallback((): Promise<OpResult<unknown>> => {
    // A second start while running returns the same promise (refused).
    if (flightRef.current !== null) {
      return flightRef.current;
    }
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    setState({
      running: true,
      elapsedSec: 0,
      unknownOutcome: false,
      cancelled: false,
      result: null,
      statusResult: null,
    });
    timerRef.current = window.setInterval(() => {
      setState((prev) => ({ ...prev, elapsedSec: prev.elapsedSec + 1 }));
    }, 1000);
    window.addEventListener("beforeunload", warnBeforeUnload);
    const flight = mutation
      .mutateAsync(ctrl.signal)
      .catch(() => networkResult())
      .then((res) => settle(res));
    flightRef.current = flight;
    return flight;
  }, [mutation, settle]);

  const cancel = useCallback((): void => {
    ctrlRef.current?.abort();
  }, []);

  const checkStatus = useCallback((): Promise<OpResult<unknown>> => {
    const status = getOp("ingest-status");
    if (!status) {
      return Promise.resolve(unknownOpResult());
    }
    // The ledger answer lands BESIDE the receipt, never on it: the final
    // op response stays the receipt this hook reports.
    return callOp<unknown>(status.path, { method: "GET" }).then((res) => {
      setState((prev) => ({ ...prev, statusResult: res }));
      return res;
    });
  }, []);

  return { start, cancel, checkStatus, ...state };
}
