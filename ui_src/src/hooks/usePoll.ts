// The one poll hook for status reads (the ingest ledger, the ingest
// queue, the doctor probes). Since the Phase-2 Wave 4 shared poll cache
// (hooks/pollCache.ts), N consumers of one op ride ONE network loop —
// the Desk rail's doctor poll and a page's poll of the same op share
// the loop instead of doubling the fetches. refresh() kicks that one
// loop at once. The op table (api/ops) is the one endpoint source.
import { useCallback, useEffect, useRef, useState } from "react";
import { subscribePoll } from "./pollCache";
import type { PollSnapshot } from "./pollCache";
import type { OpResult } from "../api/types";

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
  const [snapshot, setSnapshot] = useState<PollSnapshot<T>>(() => ({
    result: null,
    paused: document.hidden,
    backingOff: false,
  }));
  // The loop reads the latest body without restarting on a new object
  // identity (callers pass literals, so [opId, bodyKey] are the keys).
  const bodyRef = useRef(body);
  useEffect(() => {
    bodyRef.current = body;
  });
  const bodyKey = JSON.stringify(body ?? null);
  // The latest loop's kick, reached through a stable callback.
  const refreshRef = useRef<() => void>(() => {});
  useEffect(() => {
    const handle = subscribePoll<T>(opId, bodyRef.current, setSnapshot);
    refreshRef.current = handle.refresh;
    return handle.release;
  }, [opId, bodyKey]);
  const refresh = useCallback((): void => {
    refreshRef.current();
  }, []);
  return { result: snapshot.result, paused: snapshot.paused, backingOff: snapshot.backingOff, refresh };
}
