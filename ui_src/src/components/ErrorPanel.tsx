import { Button } from "antd";
import { strings } from "../strings";
import type { OpResult } from "../api/types";
import JsonViewer from "./JsonViewer";
import StatusBanner from "./StatusBanner";

/** A call failed (kind error or blocked): plain headline, remediation, retry. */
export default function ErrorPanel({
  result,
  onRetry,
}: {
  result: OpResult;
  onRetry?: () => void;
}) {
  const blocked = result.kind === "blocked";
  return (
    <div>
      <StatusBanner kind="error">{blocked ? strings.blockedTitle : strings.errorTitle}</StatusBanner>
      {result.message !== "" && <p>{result.message}</p>}
      {result.remediation !== "" && (
        <p>
          <strong>{strings.remediationLabel}: </strong>
          {result.remediation}
        </p>
      )}
      <JsonViewer value={result.data} />
      {onRetry && (
        <Button onClick={onRetry} style={{ marginTop: 8 }}>
          {strings.retryLabel}
        </Button>
      )}
    </div>
  );
}
