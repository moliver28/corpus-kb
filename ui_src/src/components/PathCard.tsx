import { Button, Card, Input, Typography } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import ErrorPanel from "./ErrorPanel";
import ReceiptCard from "./ReceiptCard";
import StatusBanner from "./StatusBanner";
import UnavailablePanel from "./UnavailablePanel";
import { useOp } from "../hooks/useOp";
import { strings } from "../strings";

export interface PathCardProps {
  title: string;
  body?: string;
  label: string;
  requiredMessage: string;
  /** The server's path field name (directory_path, path, file_path). */
  pathKey: string;
  /** Extra request fields beside the path (caller-validated toggles). */
  extraBody?: Record<string, unknown>;
  opId: string;
  children?: ReactNode;
}

/**
 * One typed server-path ingest form: a required path, optional toggles
 * rendered by the caller, and the single manifest op behind it. Used by
 * the folder walk (Ingest, Scale) and the transcript ingest (Ingest).
 */
export default function PathCard({
  title,
  body,
  label,
  requiredMessage,
  pathKey,
  extraBody,
  opId,
  children,
}: PathCardProps) {
  const [path, setPath] = useState("");
  const [hint, setHint] = useState("");
  const op = useOp(opId);

  async function run(): Promise<void> {
    if (path.trim() === "") {
      setHint(requiredMessage);
      return;
    }
    setHint("");
    op.reset();
    await op.run({ [pathKey]: path.trim(), ...extraBody });
  }

  let resultView: ReactNode = null;
  const result = op.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      resultView = <UnavailablePanel result={result} onRetry={() => void run()} />;
    } else if (result.ok && result.kind !== "network") {
      resultView = <ReceiptCard result={result} />;
    } else {
      resultView = <ErrorPanel result={result} onRetry={() => void run()} opId={opId} />;
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
        <Button type="primary" loading={op.running} onClick={() => void run()}>
          {strings.runnerRunLabel}
        </Button>
      </div>
      {hint !== "" && <StatusBanner kind="warn">{hint}</StatusBanner>}
      {children}
      {resultView}
    </Card>
  );
}
