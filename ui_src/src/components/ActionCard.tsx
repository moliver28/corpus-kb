import { Button, Card, Skeleton, Typography } from "antd";
import ErrorPanel from "./ErrorPanel";
import JsonViewer from "./JsonViewer";
import OpBody from "./OpBody";
import ReceiptCard from "./ReceiptCard";
import StatusBanner from "./StatusBanner";
import ConfirmGate from "./ConfirmGate";
import LongOpControls from "./LongOpControls";
import { callOp } from "../api/client";
import { getOp, statusOpFor } from "../api/ops";
import type { OpResult } from "../api/types";
import { useCopy } from "../hooks/useCopy";
import { useLongOp } from "../hooks/useLongOp";
import { useOp } from "../hooks/useOp";
import { strings } from "../strings/app";
/** One dormant-feature notice: the exact config key + a YAML snippet. */
interface DormantHint {
  configKey: string;
  yaml: string;
}

/** A dormant (config-off) answer: the exact key + a copyable YAML snippet. */
function DormantNote({ hint }: { hint: DormantHint }) {
  const { copiedKey, unavailable, copy } = useCopy();
  return (
    <div className="ui-stack">
      <StatusBanner kind="info">{strings.analyzeDormant}</StatusBanner>
      <p>
        <strong>{strings.analyzeDormantConfigKey}: </strong>
        {hint.configKey}
      </p>
      <pre className="ui-json-yaml">{hint.yaml}</pre>
      <div className="ui-actions">
        <Button onClick={() => copy(hint.yaml)}>
          {unavailable
            ? strings.copyUnavailable
            : copiedKey === "self"
              ? strings.copiedNote
              : strings.analyzeCopyYaml}
        </Button>
      </div>
    </div>
  );
}

/** The receipt side of a settled run: the ok envelope only. */
function SettledOk({ result, dormant }: { result: OpResult; dormant?: DormantHint }) {
  if (typeof result.data === "object" && result.data !== null) {
    const data = result.data as Record<string, unknown>;
    if (data.status === "disabled" && dormant !== undefined) {
      return <DormantNote hint={dormant} />;
    }
    // Honesty first: only a success envelope earns the Done receipt.
    // Anything else (disabled, partial, skipped, error) renders the
    // server's own reason and remedy as a plain-English note.
    if (typeof data.status === "string" && data.status !== "success") {
      const reason = typeof data.reason === "string" ? data.reason : result.message;
      const remedy =
        typeof data.remedy === "string" && data.remedy !== ""
          ? data.remedy
          : result.remediation;
      return (
        <div className="ui-stack">
          <StatusBanner kind="warn">
            {reason !== "" ? reason : strings.receiptNotSuccess}
          </StatusBanner>
          {remedy !== "" && (
            <StatusBanner kind="info">
              <strong>{strings.remediationLabel}: </strong>
              {remedy}
            </StatusBanner>
          )}
          <JsonViewer value={result.data} />
        </div>
      );
    }
  }
  return <ReceiptCard result={result} />;
}

interface ActionCardProps {
  opId: string;
  title: string;
  body?: string;
  dormant?: DormantHint;
}

/**
 * One guided analysis action: a card bound to one manifest op. The
 * longRunning flag comes from the manifest itself — long ops run behind
 * the confirm gate with the honest wait; short ops run directly.
 */
export default function ActionCard({ opId, title, body, dormant }: ActionCardProps) {
  const short = useOp(opId);
  const long = useLongOp(opId, (signal) => {
    const op = getOp(opId);
    return callOp<unknown>(op?.path ?? "", {
      method: op?.method,
      body: op?.method === "POST" ? {} : undefined,
      signal,
      timeoutMs: "none",
    });
  }, statusOpFor(opId));
  const isLong = getOp(opId)?.longRunning === true;

  async function run(): Promise<void> {
    if (isLong) {
      await long.start();
    } else {
      short.reset();
      // A GET or DELETE read takes no body; POST guided actions send an
      // empty one.
      await short.run(getOp(opId)?.method === "POST" ? {} : undefined);
    }
  }

  function retry(): void {
    void run();
  }

  const settled: OpResult | null = isLong ? long.result : short.result;
  const running = isLong ? long.running : short.running;

  return (
    <Card title={title}>
      {body !== undefined && (
        <Typography.Paragraph type="secondary">{body}</Typography.Paragraph>
      )}
      {running && !isLong && <Skeleton active title={false} paragraph={{ rows: 2 }} />}
      <div className="ui-actions">
        {isLong ? (
          <ConfirmGate
            title={strings.confirmLongTitle}
            description={strings.confirmLongBody}
            loading={long.running}
            onConfirm={() => void run()}
          />
        ) : (
          <Button type="primary" loading={short.running} onClick={() => void run()}>
            {strings.runnerRunLabel}
          </Button>
        )}
      </div>
      <LongOpControls op={long} onRetry={retry} />
      {settled !== null && !running && (
        <OpBody
          result={settled}
          ok={settled.ok && settled.kind !== "unknown"}
          retry={retry}
          opId={opId}
          failure={(failed) => <ErrorPanel result={failed} onRetry={retry} opId={opId} />}
        >
          <SettledOk result={settled} dormant={dormant} />
        </OpBody>
      )}
    </Card>
  );
}
