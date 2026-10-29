// The ingest-queue family (textbook program W1): typed client seam over
// the three queue ops. Path rule (zero-drift rule 1): the manifest is the
// ONE op table, so every call resolves through getOp first. The registry
// rows land with the queue surfaces' parity wave; until then the pinned
// HTTP routes keep the tab working. Both states serve the same handlers.
import { callOp } from "./client";
import { getOp } from "./ops";
import type { OpResult } from "./types";

/** One queue row as the status op serves it (queue_payload.py). */
export interface QueueEntry {
  id: string;
  path: string;
  title: string | null;
  status: string;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  elapsed_seconds: number | null;
  queued_at: string | null;
  run_id: string | null;
  project_id: string | null;
  pages_done: number | null;
  pages_total: number | null;
  chunks: number | null;
}

/** queue_status envelope: one row, or the pending + running list. */
export interface QueueStatusPayload {
  status: string;
  entries: QueueEntry[];
  count: number;
}

/** queue_enqueue envelope: idempotent per content hash. */
export interface QueueEnqueuePayload {
  status: string;
  queue_id: string;
  content_hash: string;
  deduplicated: boolean;
  state: string;
}

/** queue_cancel envelope. */
export interface QueueCancelPayload {
  status: string;
  queue_id: string;
  state: string;
}

/** The body queue_enqueue accepts (every key but path is optional). */
export interface QueueEnqueueBody {
  path: string;
  title?: string;
  profile?: string;
  strategy_config?: Record<string, unknown>;
  project_id?: string;
}

const ROUTES = {
  enqueue: "/api/queue/enqueue",
  status: "/api/queue/status",
  cancel: "/api/queue/cancel",
} as const;

function route(opId: string, fallback: string): string {
  return getOp(opId)?.path ?? fallback;
}

/** Queue one file. A repeat enqueue answers the existing row. */
export function enqueueBook(
  body: QueueEnqueueBody,
): Promise<OpResult<QueueEnqueuePayload>> {
  return callOp<QueueEnqueuePayload>(route("queue-enqueue", ROUTES.enqueue), {
    method: "POST",
    body,
  });
}

/** One row by id, or the active list when the id is absent. */
export function readQueue(queueId?: string): Promise<OpResult<QueueStatusPayload>> {
  const body: Record<string, string> = {};
  if (queueId !== undefined && queueId !== "") {
    body.queue_id = queueId;
  }
  return callOp<QueueStatusPayload>(route("queue-status", ROUTES.status), {
    method: "POST",
    body,
  });
}

/** Cancel one pending or running row; done rows answer a conflict. */
export function cancelEntry(queueId: string): Promise<OpResult<QueueCancelPayload>> {
  return callOp<QueueCancelPayload>(route("queue-cancel", ROUTES.cancel), {
    method: "POST",
    body: { queue_id: queueId },
  });
}

/** The entries of one queue-status answer (null-safe for callers). */
export function entriesOf(data: QueueStatusPayload | null): QueueEntry[] {
  if (data === null || !Array.isArray(data.entries)) {
    return [];
  }
  return data.entries;
}
