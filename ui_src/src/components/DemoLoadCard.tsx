import { Button, Card, Typography } from "antd";
import { useNavigate } from "react-router";
import ErrorPanel from "../components/ErrorPanel";
import LongOpControls from "../components/LongOpControls";
import OpBody from "../components/OpBody";
import ReceiptCard from "../components/ReceiptCard";
import ConfirmGate from "../components/ConfirmGate";
import { callOp } from "../api/client";
import { getOp, statusOpFor } from "../api/ops";
import type { OpResult } from "../api/types";
import { useLongOp } from "../hooks/useLongOp";
import { strings } from "../strings/app";

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

  function retry(): void {
    void run();
  }

  const settled: OpResult | null = long.result;
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
      <LongOpControls op={long} onRetry={retry} />
      {settled !== null && (
        <OpBody
          result={settled}
          ok={settled.ok && settled.kind !== "unknown"}
          retry={retry}
          opId="demo-load"
          failure={(failed) => <ErrorPanel result={failed} onRetry={retry} />}
        >
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
        </OpBody>
      )}
    </Card>
  );
}
