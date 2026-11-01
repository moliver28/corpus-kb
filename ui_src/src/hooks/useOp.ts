// Run one manifest op through the shared client (bounded, regular calls).
import { useMutation } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { callOp } from "../api/client";
import { unknownOpResult } from "../api/errors";
import { getOp } from "../api/ops";
import type { OpResult } from "../api/types";

interface UseOp {
  run: (body?: unknown) => Promise<OpResult<unknown>>;
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
    mutationFn: async (body: unknown): Promise<OpResult<unknown>> => {
      const op = getOp(opId);
      if (!op) {
        return unknownOpResult();
      }
      return callOp<unknown>(op.path, {
        method: op.method === "GET" ? "GET" : "POST",
        body,
        timeoutMs,
      });
    },
    onSuccess: setResult,
  });
  // mutateAsync and reset are bound methods of the observer, so they
  // keep one identity across renders (the mutation object does not).
  // run and reset therefore hold stable identities too.
  const { mutateAsync, reset: resetMutation, isPending } = mutation;
  const run = useCallback((body?: unknown) => mutateAsync(body), [mutateAsync]);
  const reset = useCallback(() => {
    resetMutation();
    setResult(null);
  }, [resetMutation]);
  return { run, running: isPending, result, reset };
}
