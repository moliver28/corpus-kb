import { Button, Card, Input, Typography } from "antd";
import type { ReactNode } from "react";
import { useRef, useState } from "react";
import { callOp } from "../api/client";
import { getOp } from "../api/ops";
import type { OpResult } from "../api/types";
import ConfirmGate from "./ConfirmGate";
import ErrorPanel from "./ErrorPanel";
import JsonViewer from "./JsonViewer";
import ReceiptCard from "./ReceiptCard";
import StatusBanner from "./StatusBanner";
import UnavailablePanel from "./UnavailablePanel";
import { useLongOp } from "../hooks/useLongOp";
import { useOp } from "../hooks/useOp";
import { strings } from "../strings";

interface PathCardProps {
  title: string;
  body?: string;
  label: string;
  requiredMessage: string;
  /** The server's path field name (directory_path, path, file_path). */
  pathKey: string;
  /** Extra request fields beside the path (caller-validated toggles). */
  extraBody?: Record<string, unknown>;
  opId: string;
  /** When set, the confirm dialog carries this text (force warnings). */
  gateDescription?: string;
  children?: ReactNode;
}

/**
 * One typed server-path ingest form over ONE manifest op. The manifest's
 * longRunning flag picks the wait: long ops (crawl, directory walk,
 * research ingest) run behind the confirm gate with the honest wait —
 * elapsed, stop waiting, check status — never a 60 s abort; short ops
 * (single transcript) run directly.
 */
export default function PathCard({
  title,
  body,
  label,
  requiredMessage,
  pathKey,
  extraBody,
  opId,
  gateDescription,
  children,
}: PathCardProps) {
  const [path, setPath] = useState("");
  const [hint, setHint] = useState("");
  const bodyRef = useRef<unknown>({});
  const isLong = getOp(opId)?.longRunning === true;
  const short = useOp(opId);
  const long = useLongOp(opId, (signal) => {
    const op = getOp(opId);
    return callOp<unknown>(op?.path ?? "", {
      method: "POST",
      body: bodyRef.current,
      signal,
      timeoutMs: "none",
    });
  });

  async function run(): Promise<void> {
    if (path.trim() === "") {
      setHint(requiredMessage);
      return;
    }
    setHint("");
    bodyRef.current = { [pathKey]: path.trim(), ...extraBody };
    if (isLong) {
      await long.start();
    } else {
      short.reset();
      await short.run(bodyRef.current);
    }
  }

  function retry(): void {
    void run();
  }

  const settled: OpResult | null = isLong ? long.result : short.result;
  const running = isLong ? long.running : short.running;
  let resultView: ReactNode = null;
  if (settled !== null && !running) {
    if (settled.kind === "unavailable") {
      resultView = <UnavailablePanel result={settled} onRetry={retry} />;
    } else if (settled.kind === "unknown") {
      // A 200 with no status word is not a receipt.
      resultView = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={settled.data} />
        </div>
      );
    } else if (settled.ok && settled.kind !== "network") {
      resultView = <ReceiptCard result={settled} />;
    } else {
      resultView = <ErrorPanel result={settled} onRetry={retry} opId={opId} />;
    }
  }

  return (
    <Card title={title}>
      {body !== undefined && (
        <Typography.Paragraph type="secondary">{body}</Typography.Paragraph>
      )}
      <div className="ui-actions">
        <Input
          style={{ maxWidth: 420 }}
          value={path}
          onChange={(event) => setPath(event.target.value)}
          placeholder={label}
          onPressEnter={() => void run()}
        />
        {isLong ? (
          <ConfirmGate
            title={strings.confirmLongTitle}
            description={gateDescription ?? strings.confirmLongBody}
            loading={long.running}
            onConfirm={() => void run()}
          />
        ) : (
          <Button type="primary" loading={short.running} onClick={() => void run()}>
            {strings.runnerRunLabel}
          </Button>
        )}
      </div>
      {hint !== "" && <StatusBanner kind="warn">{hint}</StatusBanner>}
      {children}
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
