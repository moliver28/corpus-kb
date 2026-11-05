import { Card, Typography } from "antd";
import { callOp } from "../../api/client";
import { getOp } from "../../api/ops";
import type { OpResult } from "../../api/types";
import { useLongOp } from "../../hooks/useLongOp";
import ConfirmGate from "../../components/ConfirmGate";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import LongOpControls from "../../components/LongOpControls";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { strings } from "../../strings";

/** The demo receipt (tools/demo_tools.py research_demo). */
interface DemoEnvelope {
  status: string;
  exit_code: number;
  narration: string;
}

function isDemoEnvelope(value: unknown): value is DemoEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.status === "string" &&
    typeof v.exit_code === "number" &&
    Number.isFinite(v.exit_code) &&
    typeof v.narration === "string"
  );
}

/** The demo's own narration, in the shared machine-voice block. */
function Narration({ text }: { text: string }) {
  if (text.trim() === "") {
    return null;
  }
  return (
    <div className="ui-stack-gap">
      <Typography.Paragraph type="secondary" className="ui-flush">
        {strings.scaleDemoNarration}
      </Typography.Paragraph>
      <div className="ui-json">
        <pre>{text}</pre>
      </div>
    </div>
  );
}

/**
 * The research demo (POST /api/research/demo): one gated long run of
 * the narrated sample pipeline. The narration is the receipt's core,
 * so the envelope guard passes it through on every outcome; the run
 * has no status ledger, so recovery is "Run again".
 */
export default function ResearchDemoCard() {
  const demo = useLongOp("research-demo", (signal) => {
    const op = getOp("research-demo");
    return callOp<unknown>(op?.path ?? "", {
      method: "POST",
      body: {},
      signal,
      timeoutMs: "none",
    });
  }, null);

  async function run(): Promise<void> {
    await demo.start();
  }

  const settled: OpResult | null = demo.result;
  const data = settled !== null && isDemoEnvelope(settled.data) ? settled.data : null;
  let resultView = null;
  if (settled !== null) {
    if (data !== null && data.status === "completed") {
      resultView = (
        <div className="ui-stack">
          <StatusBanner kind="ok">{strings.scaleDemoCompleted}</StatusBanner>
          <Narration text={data.narration} />
        </div>
      );
    } else if (data !== null && data.status === "failed") {
      resultView = (
        <div className="ui-stack">
          <StatusBanner kind="error">{strings.scaleDemoFailed}</StatusBanner>
          <Narration text={data.narration} />
        </div>
      );
    } else if (settled.kind === "unavailable") {
      resultView = (
        <div className="ui-stack">
          <UnavailablePanel result={settled} onRetry={() => void run()} />
          {data !== null && <Narration text={data.narration} />}
        </div>
      );
    } else if (data !== null) {
      // A known envelope with an unexpected status word stays honest.
      resultView = (
        <div className="ui-stack">
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={settled.data} />
        </div>
      );
    } else {
      resultView = <ErrorPanel result={settled} onRetry={() => void run()} />;
    }
  }

  return (
    <Card title={strings.scaleDemoTitle}>
      <Typography.Paragraph type="secondary">{strings.scaleDemoBody}</Typography.Paragraph>
      <div className="ui-actions">
        <ConfirmGate
          title={strings.scaleDemoConfirmTitle}
          description={strings.scaleDemoConfirmBody}
          confirmLabel={strings.scaleDemoAction}
          loading={demo.running}
          onConfirm={() => void run()}
        />
      </div>
      <LongOpControls op={demo} onRetry={() => void run()} />
      {resultView}
    </Card>
  );
}
