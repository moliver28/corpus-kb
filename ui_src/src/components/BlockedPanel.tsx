import { Button, Typography } from "antd";
import { strings } from "../strings";
import type { OpResult } from "../api/types";
import JsonViewer from "./JsonViewer";
import StatusBanner from "./StatusBanner";

/**
 * A blocked release (HTTP 409 or a blocked envelope): the structured
 * refusal — the reason and the unmet required gates by name — never a
 * generic error panel.
 */
export default function BlockedPanel({
  result,
  onRetry,
}: {
  result: OpResult;
  onRetry?: () => void;
}) {
  const data =
    typeof result.data === "object" && result.data !== null
      ? (result.data as Record<string, unknown>)
      : {};
  const unmet = Array.isArray(data.unmet_required_gates)
    ? data.unmet_required_gates.filter((gate): gate is string => typeof gate === "string")
    : [];
  const reason = typeof data.reason === "string" ? data.reason : result.message;
  return (
    <div>
      <StatusBanner kind="error">{strings.releaseBlockedTitle}</StatusBanner>
      {reason !== "" && (
        <p>
          <strong>{strings.releaseBlockedReason}: </strong>
          {reason}
        </p>
      )}
      {unmet.length > 0 && (
        <>
          <Typography.Text strong>{strings.releaseBlockedGates}</Typography.Text>
          <ul className="ui-citations">
            {unmet.map((gate) => (
              <li key={gate}>{gate}</li>
            ))}
          </ul>
        </>
      )}
      <JsonViewer value={result.data} />
      {onRetry && (
        <div className="ui-actions">
          <Button onClick={onRetry}>{strings.retryLabel}</Button>
        </div>
      )}
    </div>
  );
}
