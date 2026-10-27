import { Button, Card, Typography } from "antd";
import type { ReactNode } from "react";

import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import ConfirmGate from "../../components/ConfirmGate";
import UnavailablePanel from "../../components/UnavailablePanel";
import { callOp } from "../../api/client";
import { getOp } from "../../api/ops";
import { useLongOp } from "../../hooks/useLongOp";
import { strings } from "../../strings";

interface CycleEnvelope {
  status: string;
  gate: unknown;
}

function isCycleEnvelope(value: unknown): value is CycleEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.status === "string" && v.status !== "" && typeof v.exit_code === "number";
}

/** One plain-English outcome per receipt status; codes stay in the raw view. */
function outcomeWord(status: string): string {
  switch (status) {
    case "completed":
      return strings.codebookCycleCompleted;
    case "unavailable":
      return strings.codebookCycleUnavailable;
    case "halted":
      return strings.codebookCycleHalted;
    case "awaiting_approval":
      return strings.codebookCycleAwaiting;
    case "approval_denied":
      return strings.codebookCycleDenied;
    default:
      return strings.codebookCycleStopped;
  }
}

/**
 * The research cycle wizard: mode "in", one stage per click. The tool
 * never auto-promotes and approve is never sent from this screen — the
 * promotion work happens in the promote gate above.
 */
export default function CycleCard() {
  const cycle = useLongOp("research-cycle", (signal) => {
    const op = getOp("research-cycle");
    return callOp<unknown>(op?.path ?? "", {
      method: "POST",
      body: { mode: "in" },
      signal,
      timeoutMs: "none",
    });
  });

  async function run(): Promise<void> {
    await cycle.start();
  }

  const settled = cycle.result;
  let outcome: ReactNode = null;
  if (settled !== null) {
    if (settled.kind === "unavailable") {
      outcome = <UnavailablePanel result={settled} onRetry={() => void run()} />;
    } else if (settled.ok && isCycleEnvelope(settled.data)) {
      const gate = settled.data.gate;
      outcome = (
        <div className="ui-stack">
          <StatusBanner
            kind={settled.data.status === "completed" ? "ok" : "warn"}
          >
            {outcomeWord(settled.data.status)}
          </StatusBanner>
          {typeof gate === "string" && gate !== "" && (
            <Typography.Text type="secondary">
              {strings.codebookCycleGate}: {gate}
            </Typography.Text>
          )}
          <JsonViewer value={settled.data} />
        </div>
      );
    } else if (settled.ok) {
      outcome = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={settled.data} />
        </div>
      );
    } else {
      outcome = <ErrorPanel result={settled} onRetry={() => void run()} opId="research-cycle" />;
    }
  }

  return (
    <Card title={strings.codebookCycleTitle}>
      <Typography.Paragraph type="secondary">{strings.codebookCycleBody}</Typography.Paragraph>
      <div className="ui-actions">
        <ConfirmGate
          title={strings.confirmLongTitle}
          description={strings.confirmLongBody}
          confirmLabel={strings.codebookCycleAction}
          loading={cycle.running}
          onConfirm={() => void run()}
        />
      </div>
      {cycle.running && (
        <div className="ui-actions">
          <StatusBanner kind="info">
            {strings.runnerRunningElapsed.replace("{sec}", String(cycle.elapsedSec))}
          </StatusBanner>
          <Button onClick={() => cycle.cancel()}>{strings.runnerCancelLabel}</Button>
        </div>
      )}
      {cycle.cancelled && <StatusBanner kind="warn">{strings.runnerCancelled}</StatusBanner>}
      {cycle.unknownOutcome && (
        <div className="ui-actions">
          <StatusBanner kind="warn">{strings.runnerUnknownOutcome}</StatusBanner>
          <Button onClick={() => void cycle.checkStatus()}>{strings.runnerCheckStatus}</Button>
        </div>
      )}
      {outcome}
    </Card>
  );
}
