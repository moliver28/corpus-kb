import { Button } from "antd";
import type { LongOp } from "../hooks/useLongOp";
import { strings } from "../strings/app";
import StatusBanner from "./StatusBanner";

/**
 * The next step after a wait ends without a receipt: check the status
 * ledger when the op family has one, or run the op again when the
 * caller offers a retry. Without either, no button renders.
 */
function RecoveryButton({ op, onRetry }: { op: LongOp; onRetry?: () => void }) {
  if (op.hasStatus) {
    return <Button onClick={() => void op.checkStatus()}>{strings.runnerCheckStatus}</Button>;
  }
  if (onRetry === undefined) {
    return null;
  }
  return <Button onClick={onRetry}>{strings.runnerRunAgain}</Button>;
}

/**
 * The one honest wait for a long op, shared by every long-op card:
 * elapsed time, stop waiting, the stopped banner, the unknown-outcome
 * block, and the status line. The ledger answer lands beside the
 * receipt, never on it. Renders nothing while the op is idle.
 */
export default function LongOpControls({
  op,
  onRetry,
  onCancel,
}: {
  op: LongOp;
  /** Retry for a family with no status op ("Run again"). */
  onRetry?: () => void;
  /** Replaces the cancel click when the card records cancel intent. */
  onCancel?: () => void;
}) {
  return (
    <>
      {op.running && (
        <div className="ui-actions">
          <StatusBanner kind="info">
            {strings.runnerRunningElapsed.replace("{sec}", String(op.elapsedSec))}
          </StatusBanner>
          <Button onClick={onCancel ?? op.cancel}>{strings.runnerCancelLabel}</Button>
        </div>
      )}
      {op.cancelled && (
        <div className="ui-actions">
          <StatusBanner kind="warn">{strings.runnerCancelled}</StatusBanner>
          <RecoveryButton op={op} onRetry={onRetry} />
        </div>
      )}
      {op.unknownOutcome && (
        <div className="ui-actions">
          <StatusBanner kind="warn">{strings.runnerUnknownOutcome}</StatusBanner>
          <RecoveryButton op={op} onRetry={onRetry} />
        </div>
      )}
      {op.statusResult !== null && (
        <StatusBanner kind="info">
          {op.statusResult.message !== ""
            ? strings.runnerStatusLine.replace("{message}", op.statusResult.message)
            : strings.runnerStatusEmpty}
        </StatusBanner>
      )}
    </>
  );
}
