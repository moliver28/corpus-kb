// Run one manifest op through the shared client (bounded, regular calls).
import { useMutation } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { callOp } from "../api/client";
import { unknownOpResult } from "../api/errors";
import { getOp } from "../api/ops";
import type { OpResult } from "../api/types";

/** One run's transport: a JSON body and/or query pairs on the op path. */
interface OpVars {
  body?: unknown;
  /** Query pairs appended to the op path in the caller's order. */
  query?: Record<string, string>;
  /** Values for the op path's {slot} placeholders (an unknown slot stays). */
  pathParams?: Record<string, string>;
}

/** Fill the manifest path's {slots}; a missing value leaves the slot raw. */
function fillPath(path: string, params?: Record<string, string>): string {
  if (params === undefined) {
    return path;
  }
  return path.replace(/\{([^}]+)\}/g, (slot, key: string) =>
    Object.prototype.hasOwnProperty.call(params, key) ? params[key] : slot,
  );
}

interface UseOp {
  run: (
    body?: unknown,
    query?: Record<string, string>,
    pathParams?: Record<string, string>,
  ) => Promise<OpResult<unknown>>;
  running: boolean;
  result: OpResult<unknown> | null;
  reset: () => void;
}

/**
 * The one hook for regular (non long-running) ops. The timeout default
 * comes from callOp; long ops use useLongOp with timeoutMs "none".
 */
export function useOp(opId: string, timeoutMs?: number | "none"): UseOp {
  const [result, setResult] = useState<OpResult<unknown> | null>(null);
  const mutation = useMutation({
    mutationFn: async (vars: OpVars): Promise<OpResult<unknown>> => {
      const op = getOp(opId);
      if (!op) {
        return unknownOpResult();
      }
      // The manifest's method passes straight through (GET, POST, or
      // DELETE) — the client never downgrades a declared verb. Path
      // slots take the caller's values; query pairs ride the path in
      // the caller's order (deterministic).
      const base = fillPath(op.path, vars.pathParams);
      const path =
        vars.query === undefined ? base : `${base}?${new URLSearchParams(vars.query)}`;
      return callOp<unknown>(path, {
        method: op.method,
        body: vars.body,
        timeoutMs,
      });
    },
    onSuccess: setResult,
  });
  // mutateAsync and reset are bound methods of the observer, so they
  // keep one identity across renders (the mutation object does not).
  // run and reset therefore hold stable identities too.
  const { mutateAsync, reset: resetMutation, isPending } = mutation;
  const run = useCallback(
    (body?: unknown, query?: Record<string, string>, pathParams?: Record<string, string>) =>
      mutateAsync({ body, query, pathParams }),
    [mutateAsync],
  );
  const reset = useCallback(() => {
    resetMutation();
    setResult(null);
  }, [resetMutation]);
  return { run, running: isPending, result, reset };
}
