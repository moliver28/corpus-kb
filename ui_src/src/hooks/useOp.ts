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
}

interface UseOp {
  run: (body?: unknown, query?: Record<string, string>) => Promise<OpResult<unknown>>;
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
      // DELETE) — the client never downgrades a declared verb. Query
      // pairs ride the path in the caller's order (deterministic).
      const path =
        vars.query === undefined
          ? op.path
          : `${op.path}?${new URLSearchParams(vars.query)}`;
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
    (body?: unknown, query?: Record<string, string>) => mutateAsync({ body, query }),
    [mutateAsync],
  );
  const reset = useCallback(() => {
    resetMutation();
    setResult(null);
  }, [resetMutation]);
  return { run, running: isPending, result, reset };
}
