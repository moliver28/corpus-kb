import { Button } from "antd";
import pkg from "../../package.json";
import { strings } from "../strings";
import type { OpResult } from "../api/types";
import { useCopy } from "../hooks/useCopy";
import JsonViewer from "./JsonViewer";
import StatusBanner from "./StatusBanner";

/** The diagnostic blob: one JSON string with everything a bug report needs. */
function diagnostic(result: OpResult, opId: string): string {
  return JSON.stringify(
    {
      spa: "corpus-kb-ui",
      version: pkg.version,
      opId,
      at: new Date().toISOString(),
      httpStatus: result.httpStatus,
      status: result.status,
      errorType: result.errorType,
      message: result.message,
      remediation: result.remediation,
      data: result.data,
    },
    null,
    2,
  );
}

/** A call failed (kind error or blocked): plain headline, remediation, retry. */
export default function ErrorPanel({
  result,
  onRetry,
  opId = "",
}: {
  result: OpResult;
  onRetry?: () => void;
  opId?: string;
}) {
  const blocked = result.kind === "blocked";
  const { copiedKey, unavailable, copy } = useCopy();
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
      <div className="ui-actions">
        <Button onClick={() => copy(diagnostic(result, opId))}>
          {unavailable
            ? strings.copyUnavailable
            : copiedKey === "self"
              ? strings.copiedNote
              : strings.errorCopyDiagnostic}
        </Button>
        {onRetry && <Button onClick={onRetry}>{strings.retryLabel}</Button>}
      </div>
    </div>
  );
}
