// The queue poll: POST queue/status every ui.spa.poll_ms while the
// browser tab is visible, parked on a visibilitychange wake while it is
// hidden, and a 10 s backoff after a 503. The useLedgerPoll precedent,
// plus a refresh() kick: an enqueue or a cancel wakes the loop at once.
// One loop, no parallel polls.
import { useCallback, useEffect, useRef, useState } from "react";
import { readQueue } from "../../api/queue";
import { runtimeConfig } from "../../api/runtimeConfig";
import type { OpResult } from "../../api/types";
import type { QueueStatusPayload } from "../../api/queue";

export const QUEUE_BACKOFF_MS = 10_000;

export interface QueuePoll {
  /** The most recent queue answer (null until the first poll lands). */
  result: OpResult<QueueStatusPayload> | null;
  /** True while the browser tab is hidden (no fetches go out). */
  paused: boolean;
  /** True after a 503, until a poll answers again. */
  backingOff: boolean;
  /** One immediate refresh; safe to call any time, never mid-poll. */
  refresh: () => void;
}

export function useQueuePoll(): QueuePoll {
  const [result, setResult] = useState<OpResult<QueueStatusPayload> | null>(null);
  const [paused, setPaused] = useState(document.hidden);
  const [backingOff, setBackingOff] = useState(false);
  // The sleep resolves early on a wake; the dirty flag covers a refresh
  // that lands while a poll is in flight (it re-polls right after).
  const wakeRef = useRef<(() => void) | null>(null);
  const dirtyRef = useRef(false);

  useEffect(() => {
    let stopped = false;
    const sleepOrWake = (ms: number): Promise<void> =>
      new Promise<void>((resolve) => {
        const timer = window.setTimeout(done, ms);
        function done(): void {
          wakeRef.current = null;
          window.clearTimeout(timer);
          resolve();
        }
        wakeRef.current = () => done();
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
        // A poll reads the freshest state there is, so a refresh that
        // fired before this moment is answered by THIS read, not another.
        dirtyRef.current = false;
        const res = await readQueue();
        if (stopped) {
          return;
        }
        setResult(res);
        const backoff = res.httpStatus === 503 || res.kind === "unavailable";
        setBackingOff(backoff);
        // A refresh during the read asks for one more pass right away.
        if (dirtyRef.current) {
          continue;
        }
        await sleepOrWake(backoff ? QUEUE_BACKOFF_MS : runtimeConfig().pollMs);
      }
    })();
    return () => {
      stopped = true;
      wakeRef.current = null;
    };
  }, []);

  const refresh = useCallback((): void => {
    dirtyRef.current = true;
    wakeRef.current?.();
  }, []);

  return { result, paused, backingOff, refresh };
}
