import { Card } from "antd";
import { strings } from "../strings/app";
import type { OpResult } from "../api/types";
import JsonViewer from "./JsonViewer";
import StatusBanner from "./StatusBanner";

// The server's closed neutral family: words that did not run or did not
// finish whole. Each carries its own honest headline, so they never ride
// the open completion classification.
const NOT_RUN: ReadonlySet<string> = new Set(["disabled", "skipped"]);

/**
 * Map the envelope to one plain headline (jargon defense). The neutral
 * words above name themselves; the open completion family (success, ok,
 * review_packet, requested, compared, released, evidence, answer, and
 * words the server coins later) rides ONE classification point: the
 * shared normalizer's ok (api/errors.ts) — never a hand word-list.
 */
function headline(result: OpResult): string {
  switch (result.status) {
    case "":
    case "unknown":
      // A status-less artifact (e.g. the governance report; the
      // normalizer rewrites an absent status to "unknown") is not a
      // completed op — never claim "Done" for it.
      return strings.receiptEmpty;
    case "empty":
      return strings.receiptEmpty;
    case "degraded":
      return strings.receiptDegraded;
    case "partial":
      return strings.receiptPartial;
    case "abstained":
      return strings.receiptAbstained;
    case "disabled":
    case "skipped":
      return strings.receiptNotSuccess;
    default:
      return result.ok ? strings.receiptDone : strings.receiptNotSuccess;
  }
}

/** The receipt for a finished op: plain headline, notes, raw answer hidden. */
export default function ReceiptCard({ result }: { result: OpResult }) {
  const message = result.message;
  const data =
    typeof result.data === "object" && result.data !== null
      ? (result.data as Record<string, unknown>)
      : {};
  const note = NOT_RUN.has(result.status) || !result.ok;
  const reason =
    typeof data.reason === "string" && data.reason !== ""
      ? data.reason
      : message;
  const remedy =
    typeof data.remedy === "string" && data.remedy !== ""
      ? data.remedy
      : result.remediation;
  // Ingestion facts the pipeline attaches when they ran (both optional in
  // the envelope): the validation summary and the MinHash dedup drops.
  const validation =
    typeof data.validation === "object" && data.validation !== null
      ? (data.validation as Record<string, unknown>)
      : null;
  const problemCount =
    validation !== null && typeof validation.problem_count === "number"
      ? validation.problem_count
      : null;
  const dedupDropped = Array.isArray(data.dedup_dropped)
    ? data.dedup_dropped.length
    : 0;
  return (
    <Card title={headline(result)} size="small">
      {result.status === "degraded" && (
        <StatusBanner kind="warn">{strings.receiptDegraded}</StatusBanner>
      )}
      {validation !== null && problemCount !== null && (
        <StatusBanner kind={problemCount === 0 ? "ok" : "warn"}>
          <strong>{strings.receiptValidationLabel}: </strong>
          {problemCount === 0
            ? strings.receiptValidationOk
            : strings.receiptValidationFindings.replace(
                "{n}",
                String(problemCount),
              )}
        </StatusBanner>
      )}
      {dedupDropped > 0 && (
        <StatusBanner kind="info">
          {strings.receiptDedupDropped.replace("{n}", String(dedupDropped))}
        </StatusBanner>
      )}
      {note && (
        <>
          <StatusBanner kind="warn">
            {reason !== "" ? reason : strings.receiptNotSuccess}
          </StatusBanner>
          {remedy !== "" && (
            <StatusBanner kind="info">
              <strong>{strings.remediationLabel}: </strong>
              {remedy}
            </StatusBanner>
          )}
        </>
      )}
      {message !== "" && !note && <p>{message}</p>}
      <JsonViewer value={result.data} />
    </Card>
  );
}
