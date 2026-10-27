import { Button } from "antd";
import { useEffect, useRef, useState } from "react";
import pkg from "../../package.json";
import { strings } from "../strings";
import type { OpResult } from "../api/types";
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
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  // The copied label flips back on its own; the timer never survives us.
  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    },
    [],
  );

  function copy(): void {
    // jsdom and hardened browsers expose no clipboard: the button stays
    // honest (never promises what it cannot do) and never throws.
    navigator.clipboard
      ?.writeText(diagnostic(result, opId))
      .then(() => {
        setCopied(true);
        timerRef.current = window.setTimeout(() => setCopied(false), 2000);
      })
      .catch(() => undefined);
  }

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
        <Button onClick={copy}>
          {copied ? strings.errorCopied : strings.errorCopyDiagnostic}
        </Button>
        {onRetry && <Button onClick={onRetry}>{strings.retryLabel}</Button>}
      </div>
    </div>
  );
}
