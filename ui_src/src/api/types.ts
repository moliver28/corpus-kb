// Shared API types (spec D.3, UI SPA program Task 4). Payload interfaces
// live next to the screen that owns the op — never here.

/** One normalized outcome class for an API call. */
export type Kind = "ok" | "error" | "unavailable" | "blocked" | "network" | "unknown";

/**
 * The one result shape every call resolves to. Neutral envelope statuses
 * (empty, degraded, partial, abstained) on HTTP 200 stay `ok: true`.
 */
export interface OpResult<T = unknown> {
  ok: boolean;
  kind: Kind;
  httpStatus: number;
  /** The envelope's own status word, or a neutral marker when absent. */
  status: string;
  message: string;
  remediation: string;
  errorType: string;
  data: T | null;
}
