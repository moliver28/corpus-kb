import { Button, Card, Skeleton, Typography } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import ErrorPanel from "./ErrorPanel";
import JsonViewer from "./JsonViewer";
import ReceiptCard from "./ReceiptCard";
import StatusBanner from "./StatusBanner";
import ConfirmGate from "./ConfirmGate";
import UnavailablePanel from "./UnavailablePanel";
import { callOp } from "../api/client";
import { getOp } from "../api/ops";
import type { OpResult } from "../api/types";
import { useLongOp } from "../hooks/useLongOp";
import { useOp } from "../hooks/useOp";
import { strings } from "../strings";
/** One dormant-feature notice: the exact config key + a YAML snippet. */
export interface DormantHint {
  configKey: string;
  yaml: string;
}

/** A dormant (config-off) answer: the exact key + a copyable YAML snippet. */
function DormantNote({ hint }: { hint: DormantHint }) {
  const [copied, setCopied] = useState(false);
  function copy(): void {
    navigator.clipboard
      ?.writeText(hint.yaml)
      .then(() => setCopied(true))
      .catch(() => undefined);
  }
  return (
    <div className="ui-stack">
      <StatusBanner kind="info">{strings.analyzeDormant}</StatusBanner>
      <p>
        <strong>{strings.analyzeDormantConfigKey}: </strong>
        {hint.configKey}
      </p>
      <pre className="ui-json-yaml">{hint.yaml}</pre>
      <div className="ui-actions">
        <Button onClick={copy}>{copied ? strings.analyzeYamlCopied : strings.analyzeCopyYaml}</Button>
      </div>
    </div>
  );
}

/** The receipt side of a settled run, shared by both paths. */
function SettledView({
  result,
  dormant,
  onRetry,
}: {
  result: OpResult;
  dormant?: DormantHint;
  onRetry: () => void;
}) {
  if (result.kind === "unavailable") {
    return <UnavailablePanel result={result} onRetry={onRetry} />;
  }
  if (result.ok && typeof result.data === "object" && result.data !== null) {
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
  if (result.ok && result.kind !== "network") {
    return <ReceiptCard result={result} />;
  }
  return <ErrorPanel result={result} onRetry={onRetry} />;
}

export interface ActionCardProps {
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
      method: op?.method === "GET" ? "GET" : "POST",
      body: op?.method === "GET" ? undefined : {},
      signal,
      timeoutMs: "none",
    });
  });
  const isLong = getOp(opId)?.longRunning === true;

  async function run(): Promise<void> {
    if (isLong) {
      await long.start();
    } else {
      short.reset();
      // A GET read takes no body; POST guided actions send an empty one.
      await short.run(getOp(opId)?.method === "GET" ? undefined : {});
    }
  }

  function retry(): void {
    void run();
  }

  const settled: OpResult | null = isLong ? long.result : short.result;
  const running = isLong ? long.running : short.running;
  let resultView: ReactNode = null;
  if (settled !== null) {
    if (settled.kind === "unknown" && settled.ok) {
      resultView = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={settled.data} />
        </div>
      );
    } else if (running) {
      resultView = null;
    } else {
      resultView = (
        <SettledView
          result={settled}
          dormant={dormant}
          onRetry={retry}
        />
      );
    }
  }

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
      {long.running && (
        <div className="ui-actions">
          <StatusBanner kind="info">
            {strings.runnerRunningElapsed.replace("{sec}", String(long.elapsedSec))}
          </StatusBanner>
          <Button onClick={() => long.cancel()}>{strings.runnerCancelLabel}</Button>
        </div>
      )}
      {long.cancelled && <StatusBanner kind="warn">{strings.runnerCancelled}</StatusBanner>}
      {long.unknownOutcome && (
        <div className="ui-actions">
          <StatusBanner kind="warn">{strings.runnerUnknownOutcome}</StatusBanner>
          <Button onClick={() => void long.checkStatus()}>{strings.runnerCheckStatus}</Button>
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
