// One manifest read bound to a changing row id (a drawer row, a detail
// pane): the newest request wins.
import { useCallback, useRef, useState } from "react";
import type { OpResult } from "../api/types";
import { useOp } from "./useOp";

/** One settled read, tagged with the row id it asked for. */
export interface RowRead {
  rowId: string;
  result: OpResult<unknown>;
}

/**
 * A read that follows a row the user can switch quickly. Every read
 * tags its result with the row id it asked for; only a still-current
 * row may land in state, so a slow answer for a document the user
 * already left can never render under the new row's title. A POST read
 * (the graph neighbors op) carries its body alongside the tag.
 */
export function useRowRead(opId: string): {
  read: (
    rowId: string,
    opts?: {
      body?: unknown;
      query?: Record<string, string>;
      pathParams?: Record<string, string>;
    },
  ) => void;
  /** The settled result, or null while none applies to the row shown. */
  state: RowRead | null;
} {
  const { run } = useOp(opId);
  const latestRef = useRef<string | null>(null);
  const [state, setState] = useState<RowRead | null>(null);
  const read = useCallback(
    (
      rowId: string,
      opts?: {
        body?: unknown;
        query?: Record<string, string>;
        pathParams?: Record<string, string>;
      },
    ): void => {
      latestRef.current = rowId;
      void (async () => {
        const result = await run(opts?.body, opts?.query, opts?.pathParams);
        if (latestRef.current === rowId) {
          setState({ rowId, result });
        }
      })();
    },
    [run],
  );
  return { read, state };
}
