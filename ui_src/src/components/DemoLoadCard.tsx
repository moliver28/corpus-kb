import { Button, Card, Typography } from "antd";
import { useNavigate } from "react-router";
import ErrorPanel from "../components/ErrorPanel";
import JsonViewer from "../components/JsonViewer";
import LongOpControls from "../components/LongOpControls";
import ReceiptCard from "../components/ReceiptCard";
import StatusBanner from "../components/StatusBanner";
import ConfirmGate from "../components/ConfirmGate";
import UnavailablePanel from "../components/UnavailablePanel";
import { callOp } from "../api/client";
import { getOp, statusOpFor } from "../api/ops";
import type { OpResult } from "../api/types";
import { useLongOp } from "../hooks/useLongOp";
import { strings } from "../strings";

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
  }, statusOpFor("demo-load"));

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
      <LongOpControls op={long} onRetry={() => void run()} />
      {resultView}
    </Card>
  );
}
