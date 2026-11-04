// The op table (hand-written seam only — the table itself is @generated).
import { getOp } from "./ops.generated";

export { getOp, OPS } from "./ops.generated";

/**
 * The one status ledger in the backend (ingest_runs) tracks the standard
 * corpus pipeline only. Research-path runs write no ingest_runs row (the
 * domain events are their ledger), so only the standard-pipeline long
 * ops get a "Check status" button; every other family stays honest by
 * default (no button). A new longRunning op must be classified here AND
 * in the LongOpControls parity test; nothing else changes.
 */
const STATUS_OPS: Readonly<Record<string, string>> = {
  "demo-load": "ingest-status",
  "ingest-directory": "ingest-status",
  "ingest-url": "ingest-status",
};

/** The status op that tracks this op's runs, or null when none does. */
export function statusOpFor(opId: string): string | null {
  const statusOpId = STATUS_OPS[opId];
  if (statusOpId === undefined) {
    return null;
  }
  return getOp(statusOpId) !== undefined ? statusOpId : null;
}
