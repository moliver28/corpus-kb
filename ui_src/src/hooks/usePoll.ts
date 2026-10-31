// The one poll loop for status reads (the ingest ledger, the ingest
// queue): poll the op every ui.spa.poll_ms while the browser tab is
// visible, park on a visibilitychange wake while it is hidden, and back
// off for 10 s after a 503. refresh() kicks the loop at once. One loop,
// no parallel polls. The op table (api/ops) is the one endpoint source.
import { useCallback, useEffect, useRef, useState } from "react";
import { callOp } from "../api/client";
import { getOp } from "../api/ops";
import { runtimeConfig } from "../api/runtimeConfig";
import type { OpResult } from "../api/types";

export const POLL_BACKOFF_MS = 10_000;

export interface Poll<T> {
  /** The most recent answer (null until the first poll lands). */
  result: OpResult<T> | null;
  /** True while the browser tab is hidden (no fetches go out). */
  paused: boolean;
  /** True after a 503, until a poll answers again. */
  backingOff: boolean;
  /** One immediate refresh; safe to call any time, never mid-poll. */
  refresh: () => void;
}

export function usePoll<T>(opId: string, body?: unknown): Poll<T> {
  const [result, setResult] = useState<OpResult<T> | null>(null);
  const [paused, setPaused] = useState(document.hidden);
  const [backingOff, setBackingOff] = useState(false);
  // The sleep resolves early on a wake; the dirty flag covers a refresh
  // that lands while a poll is in flight (it re-polls right after).
  const wakeRef = useRef<(() => void) | null>(null);
  const dirtyRef = useRef(false);
  // The loop reads the latest body without restarting on a new object
  // identity (callers pass literals, so [opId] is the only effect key).
  const bodyRef = useRef(body);
  useEffect(() => {
    bodyRef.current = body;
  });

  useEffect(() => {
    const op = getOp(opId);
    if (op === undefined) {
      return undefined;
    }
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
        const res = await callOp<T>(op.path, {
          method: op.method === "GET" ? "GET" : "POST",
          body: bodyRef.current,
        });
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
        await sleepOrWake(backoff ? POLL_BACKOFF_MS : runtimeConfig().pollMs);
      }
    })();
    return () => {
      stopped = true;
      wakeRef.current = null;
    };
  }, [opId]);

  const refresh = useCallback((): void => {
    dirtyRef.current = true;
    wakeRef.current?.();
  }, []);

  return { result, paused, backingOff, refresh };
}
