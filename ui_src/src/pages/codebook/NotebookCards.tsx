import { Button, Card, Typography } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import LabeledField, { useFirstErrorFocus } from "../../components/LabeledField";
import ReceiptCard from "../../components/ReceiptCard";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";

/**
 * The notebook views (spec F9): evidence for one code, uncoded units,
 * and code overlap — receipts over their single manifest ops.
 */
export function EvidenceCard() {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const { rootRef, flag } = useFirstErrorFocus();
  const evidence = useOp("notebook-evidence");

  async function run(): Promise<void> {
    if (code.trim() === "") {
      setError(strings.codebookEvidenceRequired);
      flag();
      return;
    }
    setError("");
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
      <div className="ui-actions" ref={rootRef}>
        <LabeledField
          label={strings.codebookEvidenceCodeLabel}
          value={code}
          onChange={setCode}
          error={error}
          required
          name="code"
          widthClass="ui-narrow--short"
          onPressEnter={() => void run()}
        />
        <Button type="primary" loading={evidence.running} onClick={() => void run()}>
          {strings.runnerRunLabel}
        </Button>
      </div>
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
