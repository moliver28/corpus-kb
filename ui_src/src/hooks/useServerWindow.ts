// The one server-window table seam: page state, the limit+1 window
// fetch, the has-more flag, the empty-window step-back, and refresh.
// Every list read that answers limit/offset windows (no total) runs
// through this hook, so every window table behaves the same. The
// client-side cap on settled results stays pageConfig's job
// (api/table.ts); this seam is for tables the server pages itself.
import { useCallback, useEffect, useState } from "react";
import type { OpResult } from "../api/types";
import { useOp } from "./useOp";

/** The caller's envelope guard, passed as a type predicate. */
type EnvelopeGuard<Envelope> = (value: unknown) => value is Envelope;

/** The state and controls one server-window table needs. */
export interface ServerWindow<Row> {
  /** The 1-based server window the table shows. */
  page: number;
  /** The window size: the client-side cap, one row under the fetch. */
  windowSize: number;
  /** This window's rows (the fetch carries one row more, never shown). */
  rows: Row[];
  /** True when the server held a row past this window. */
  hasMore: boolean;
  /** The raw read result (OpBody renders the honest states). */
  result: OpResult<unknown> | null;
  /** True when the settled payload passed the caller's guard. */
  okEnvelope: boolean;
  /** Show another window (the pager's onChange). */
  goto(page: number): void;
  /** Drop the current answer and read this window again. */
  refresh(): void;
  /**
   * After a delete: when the removed row was this window's only row,
   * step back one window, so the pager never rests on an empty page;
   * otherwise read this window again.
   */
  stepBackAfterDelete(shownCount: number): void;
}

export function useServerWindow<Row, Envelope extends { result: Row[] }>(
  opId: string,
  windowSize: number,
  isEnvelope: EnvelopeGuard<Envelope>,
): ServerWindow<Row> {
  const [page, setPage] = useState(1);
  const list = useOp(opId);
  const { run: runList } = list;

  const loadPage = useCallback(
    (next: number): void => {
      void runList(undefined, {
        limit: String(windowSize + 1),
        offset: String((next - 1) * windowSize),
      });
    },
    [runList, windowSize],
  );

  useEffect(() => {
    loadPage(page);
  }, [loadPage, page]);

  function goto(next: number): void {
    setPage(next);
  }

  function refresh(): void {
    list.reset();
    loadPage(page);
  }

  function stepBackAfterDelete(shownCount: number): void {
    if (shownCount === 1 && page > 1) {
      const prev = page - 1;
      setPage(prev);
      loadPage(prev);
      return;
    }
    refresh();
  }

  const result = list.result;
  const data = result !== null && isEnvelope(result.data) ? result.data : null;
  const rows = (data?.result ?? []).slice(0, windowSize);
  const hasMore = data !== null && data.result.length > windowSize;

  return {
    page,
    windowSize,
    rows,
    hasMore,
    result,
    okEnvelope: data !== null,
    goto,
    refresh,
    stepBackAfterDelete,
  };
}

/**
 * The window pager's total: every row shown so far, plus one when a
 * next window exists. antd renders the next page key from this total —
 * the server has no count, the window is the honest pager.
 */
export function windowPagerTotal(
  page: number,
  windowSize: number,
  shownCount: number,
  hasMore: boolean,
): number {
  return (page - 1) * windowSize + shownCount + (hasMore ? 1 : 0);
}
