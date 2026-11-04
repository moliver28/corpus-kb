// The op table (hand-written seam only — the table itself is @generated).
import { getOp } from "./ops.generated";

export { getOp, OPS } from "./ops.generated";

/**
 * The one status ledger in the backend tracks ingest runs. Only the op
 * families whose runs land in that ledger get a "Check status" button;
 * every other family stays honest by default (no button). A new ingest
 * op adds one row here; nothing else changes.
 */
const STATUS_OPS: Readonly<Record<string, string>> = {
  "demo-load": "ingest-status",
  "ingest-directory": "ingest-status",
  "ingest-url": "ingest-status",
  "research-ingest": "ingest-status",
};

/** The status op that tracks this op's runs, or null when none does. */
export function statusOpFor(opId: string): string | null {
  const statusOpId = STATUS_OPS[opId];
  if (statusOpId === undefined) {
    return null;
  }
  return getOp(statusOpId) !== undefined ? statusOpId : null;
}
