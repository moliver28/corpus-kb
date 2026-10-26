// Run one manifest op through the shared client (bounded, regular calls).
import { useMutation } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { callOp } from "../api/client";
import { unknownOpResult } from "../api/errors";
import { getOp } from "../api/ops";
import type { OpResult } from "../api/types";

export interface UseOp {
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
  const run = useCallback((body?: unknown) => mutation.mutateAsync(body), [mutation]);
  const reset = useCallback(() => {
    mutation.reset();
    setResult(null);
  }, [mutation]);
  return { run, running: mutation.isPending, result, reset };
}
