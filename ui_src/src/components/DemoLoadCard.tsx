import { Button, Card, Typography } from "antd";
import { useNavigate } from "react-router";
import ErrorPanel from "../components/ErrorPanel";
import JsonViewer from "../components/JsonViewer";
import ReceiptCard from "../components/ReceiptCard";
import StatusBanner from "../components/StatusBanner";
import ConfirmGate from "../components/ConfirmGate";
import UnavailablePanel from "../components/UnavailablePanel";
import { callOp } from "../api/client";
import { getOp } from "../api/ops";
import type { OpResult } from "../api/types";
import { useLongOp } from "../hooks/useLongOp";
import { strings } from "../strings";

// The degraded demo receipt: the status word stays "success" and the
// marker rides INSIDE result (the DEMO_KEYS contract). A missing or
// junk cell reads as a clean run — the server adds the key only on a
// degraded run.
function hasDegradedResult(result: OpResult): boolean {
  if (typeof result.data !== "object" || result.data === null) return false;
  const envelope = result.data as Record<string, unknown>;
  if (typeof envelope.result !== "object" || envelope.result === null) return false;
  const inner = envelope.result as Record<string, unknown>;
  return inner.degraded === true;
}

/**
 * The sample-knowledge action: the demo-load op behind a confirm gate.
 * No parameters — the server picks the bundled folder. The receipt is
 * the server envelope as-is; a partial run lists its failures there.
 */
export default function DemoLoadCard() {
  const navigate = useNavigate();
  const long = useLongOp("demo-load", (signal) => {
    const op = getOp("demo-load");
    return callOp<unknown>(op?.path ?? "", {
      method: "POST",
      body: {},
      signal,
      timeoutMs: "none",
    });
  });

  async function run(): Promise<void> {
    await long.start();
  }

  const settled: OpResult | null = long.result;
  let resultView = null;
  if (settled !== null) {
    if (settled.kind === "unavailable") {
      resultView = <UnavailablePanel result={settled} onRetry={() => void run()} />;
    } else if (settled.kind === "unknown") {
      // An envelope without a status word is not a receipt: show the
      // honest unreadable panel, never a "Done" headline.
      resultView = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={settled.data} />
        </div>
      );
    } else if (settled.ok && settled.kind !== "network") {
      resultView = (
        <div className="ui-stack">
          {/* A degraded run never reads as a clean one: the note sits
              above the receipt headline. */}
          {hasDegradedResult(settled) && (
            <StatusBanner kind="warn">{strings.setupDemoDegraded}</StatusBanner>
          )}
          <ReceiptCard result={settled} />
          {/* The jump only shows on a clean run; a partial run keeps the
              viewer on the receipt with its failure list. */}
          {settled.status === "success" && (
            <Button type="primary" onClick={() => navigate("/ask")}>
              {strings.setupDemoNext}
            </Button>
          )}
        </div>
      );
    } else {
      resultView = <ErrorPanel result={settled} onRetry={() => void run()} />;
    }
  }

  return (
    <Card title={strings.setupDemoTitle}>
      <Typography.Paragraph type="secondary">{strings.setupDemoBody}</Typography.Paragraph>
      <div className="ui-actions">
        <ConfirmGate
          title={strings.setupDemoConfirmTitle}
          description={strings.setupDemoConfirmBody}
          confirmLabel={strings.setupDemoAction}
          loading={long.running}
          onConfirm={() => void run()}
        />
      </div>
      {long.running && (
        <div className="ui-actions">
          <StatusBanner kind="info">
            {strings.runnerRunningElapsed.replace("{sec}", String(long.elapsedSec))}
          </StatusBanner>
          <Button onClick={() => long.cancel()}>{strings.runnerCancelLabel}</Button>
        </div>
      )}
      {long.cancelled && (
        <div className="ui-actions">
          {/* The user stopped waiting; the server may still be working.
              No error panel, no retry — status polling stays available. */}
          <StatusBanner kind="warn">{strings.runnerCancelled}</StatusBanner>
          <Button onClick={() => void long.checkStatus()}>{strings.runnerCheckStatus}</Button>
        </div>
      )}
      {long.unknownOutcome && (
        <div className="ui-actions">
          <StatusBanner kind="warn">{strings.runnerUnknownOutcome}</StatusBanner>
          <Button onClick={() => void long.checkStatus()}>
            {strings.runnerCheckStatus}
          </Button>
        </div>
      )}
      {long.statusResult !== null && (
        <StatusBanner kind="info">
          {long.statusResult.message !== ""
            ? strings.runnerStatusLine.replace("{message}", long.statusResult.message)
            : strings.runnerStatusEmpty}
        </StatusBanner>
      )}
      {resultView}
    </Card>
  );
}
