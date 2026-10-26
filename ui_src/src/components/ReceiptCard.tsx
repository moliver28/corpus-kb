import { Card } from "antd";
import { strings } from "../strings";
import type { OpResult } from "../api/types";
import JsonViewer from "./JsonViewer";
import StatusBanner from "./StatusBanner";

/** Map the envelope status word to one plain headline (jargon defense). */
function headline(status: string): string {
  switch (status) {
    case "empty":
      return strings.receiptEmpty;
    case "degraded":
      return strings.receiptDegraded;
    case "partial":
      return strings.receiptPartial;
    case "abstained":
      return strings.receiptAbstained;
    default:
      return strings.receiptDone;
  }
}

/** The receipt for a finished op: plain headline, notes, raw answer hidden. */
export default function ReceiptCard({ result }: { result: OpResult }) {
  const message = result.message;
  return (
    <Card title={headline(result.status)} size="small">
      {result.status === "degraded" && (
        <StatusBanner kind="warn">{strings.receiptDegraded}</StatusBanner>
      )}
      {message !== "" && <p>{message}</p>}
      <JsonViewer value={result.data} />
    </Card>
  );
}
