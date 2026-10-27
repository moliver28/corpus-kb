// The ledger poll (spec F6): GET ingest/status every ui.spa.poll_ms
// while the browser tab is visible, parked on a visibilitychange wake
// while it is hidden (no busy polling), and a 10 s backoff after a 503.
// One loop, no parallel polls.
import { useEffect, useState } from "react";
import { callOp } from "../../api/client";
import { getOp } from "../../api/ops";
import { runtimeConfig } from "../../api/runtimeConfig";
import type { OpResult } from "../../api/types";

export const BACKOFF_MS = 10_000;

export interface LedgerPoll {
  /** The most recent ledger answer (null until the first poll lands). */
  result: OpResult | null;
  /** True while the browser tab is hidden (no fetches go out). */
  paused: boolean;
  /** True after a 503, until a poll answers again. */
  backingOff: boolean;
}

export function useLedgerPoll(): LedgerPoll {
  const [result, setResult] = useState<OpResult | null>(null);
  const [paused, setPaused] = useState(document.hidden);
  const [backingOff, setBackingOff] = useState(false);

  useEffect(() => {
    const status = getOp("ingest-status");
    if (status === undefined) {
      return undefined;
    }
    let stopped = false;
    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        window.setTimeout(resolve, ms);
      });
    // The hidden park: the loop awaits this promise until the tab
    // becomes visible again — a visibilitychange wake, not a timer.
    const untilVisible = () =>
      new Promise<void>((resolve) => {
        const onChange = () => {
          if (stopped) {
            document.removeEventListener("visibilitychange", onChange);
            return;
          }
          if (!document.hidden) {
            document.removeEventListener("visibilitychange", onChange);
            resolve();
          }
        };
        document.addEventListener("visibilitychange", onChange);
      });
    void (async () => {
      while (!stopped) {
        if (document.hidden) {
          setPaused(true);
          await untilVisible();
          if (stopped) {
            return;
          }
        }
        setPaused(false);
        const res = await callOp<unknown>(status.path, { method: "GET" });
        if (stopped) {
          return;
        }
        setResult(res);
        const backoff = res.httpStatus === 503 || res.kind === "unavailable";
        setBackingOff(backoff);
        await sleep(backoff ? BACKOFF_MS : runtimeConfig().pollMs);
      }
    })();
    return () => {
      stopped = true;
    };
  }, []);

  return { result, paused, backingOff };
}
