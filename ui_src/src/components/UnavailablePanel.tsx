import { Button } from "antd";
import { strings } from "../strings";
import type { OpResult } from "../api/types";
import StatusBanner from "./StatusBanner";

/** The server cannot run this yet (kind unavailable). Retry stays honest. */
export default function UnavailablePanel({
  result,
  onRetry,
}: {
  result: OpResult;
  onRetry?: () => void;
}) {
  return (
    <div>
      <StatusBanner kind="warn">{strings.unavailableTitle}</StatusBanner>
      <p>{strings.unavailableBody}</p>
      {result.message !== "" && <p>{result.message}</p>}
      {result.remediation !== "" && (
        <p>
          <strong>{strings.remediationLabel}: </strong>
          {result.remediation}
        </p>
      )}
      {onRetry && (
        <Button onClick={onRetry} className="ui-stack-gap">
          {strings.retryLabel}
        </Button>
      )}
    </div>
  );
}
