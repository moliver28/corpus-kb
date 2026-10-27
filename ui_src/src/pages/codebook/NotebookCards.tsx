import { Button, Card, Input, Typography } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import ReceiptCard from "../../components/ReceiptCard";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";

/**
 * The notebook views (spec F9): evidence for one code, uncoded units,
 * and code overlap — receipts over their single manifest ops.
 */
export function EvidenceCard() {
  const [code, setCode] = useState("");
  const [hint, setHint] = useState("");
  const evidence = useOp("notebook-evidence");

  async function run(): Promise<void> {
    if (code.trim() === "") {
      setHint(strings.codebookEvidenceRequired);
      return;
    }
    setHint("");
    evidence.reset();
    await evidence.run({ code: code.trim() });
  }

  let body: ReactNode = null;
  const result = evidence.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={() => void run()} />;
    } else if (result.ok && result.kind !== "network") {
      body = <ReceiptCard result={result} />;
    } else {
      body = <ErrorPanel result={result} onRetry={() => void run()} opId="notebook-evidence" />;
    }
  }

  return (
    <Card title={strings.codebookEvidenceTitle}>
      <div className="ui-actions">
        <Input
          style={{ maxWidth: 360 }}
          value={code}
          onChange={(event) => setCode(event.target.value)}
          placeholder={strings.codebookEvidenceCodeLabel}
          onPressEnter={() => void run()}
        />
        <Button type="primary" loading={evidence.running} onClick={() => void run()}>
          {strings.runnerRunLabel}
        </Button>
      </div>
      {hint !== "" && <StatusBanner kind="warn">{hint}</StatusBanner>}
      {body}
    </Card>
  );
}

export function UncodedCard() {
  const uncoded = useOp("notebook-uncoded");
  let body: ReactNode = null;
  const result = uncoded.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={() => void uncoded.run({})} />;
    } else if (result.ok && result.kind !== "network") {
      body = <ReceiptCard result={result} />;
    } else {
      body = <ErrorPanel result={result} onRetry={() => void uncoded.run({})} opId="notebook-uncoded" />;
    }
  }
  return (
    <Card title={strings.codebookUncodedTitle}>
      <Typography.Paragraph type="secondary">{strings.codebookUncodedBody}</Typography.Paragraph>
      <div className="ui-actions">
        <Button loading={uncoded.running} onClick={() => void uncoded.run({})}>
          {strings.runnerRunLabel}
        </Button>
      </div>
      {body}
    </Card>
  );
}

export function OverlapCard() {
  const overlap = useOp("notebook-overlap");
  let body: ReactNode = null;
  const result = overlap.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={() => void overlap.run({})} />;
    } else if (result.ok && result.kind !== "network") {
      body = <ReceiptCard result={result} />;
    } else {
      body = <ErrorPanel result={result} onRetry={() => void overlap.run({})} opId="notebook-overlap" />;
    }
  }
  return (
    <Card title={strings.codebookOverlapTitle}>
      <Typography.Paragraph type="secondary">{strings.codebookOverlapBody}</Typography.Paragraph>
      <div className="ui-actions">
        <Button loading={overlap.running} onClick={() => void overlap.run({})}>
          {strings.runnerRunLabel}
        </Button>
      </div>
      {body}
    </Card>
  );
}
