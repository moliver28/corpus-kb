import { Button, Card, Typography } from "antd";
import type { ReactNode } from "react";
import { useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import LabeledField, { useFirstErrorFocus } from "../../components/LabeledField";
import OpBody from "../../components/OpBody";
import ReceiptCard from "../../components/ReceiptCard";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings/codebook";

/**
 * The notebook views (spec F9): evidence for one code, uncoded units,
 * and code overlap — receipts over their single manifest ops, each
 * dispatched through the one honest body.
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

  const body: ReactNode =
    evidence.result !== null ? (
      <OpBody
        result={evidence.result}
        ok={evidence.result.ok && evidence.result.kind !== "unknown"}
        retry={() => void run()}
        opId="notebook-evidence"
        failure={(failed) => (
          <ErrorPanel result={failed} onRetry={() => void run()} opId="notebook-evidence" />
        )}
      >
        <ReceiptCard result={evidence.result} />
      </OpBody>
    ) : null;

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

  function run(): void {
    uncoded.reset();
    void uncoded.run({});
  }

  const body: ReactNode =
    uncoded.result !== null ? (
      <OpBody
        result={uncoded.result}
        ok={uncoded.result.ok && uncoded.result.kind !== "unknown"}
        retry={run}
        opId="notebook-uncoded"
        failure={(failed) => <ErrorPanel result={failed} onRetry={run} opId="notebook-uncoded" />}
      >
        <ReceiptCard result={uncoded.result} />
      </OpBody>
    ) : null;

  return (
    <Card title={strings.codebookUncodedTitle}>
      <Typography.Paragraph type="secondary">{strings.codebookUncodedBody}</Typography.Paragraph>
      <div className="ui-actions">
        <Button loading={uncoded.running} onClick={run}>
          {strings.runnerRunLabel}
        </Button>
      </div>
      {body}
    </Card>
  );
}

export function OverlapCard() {
  const overlap = useOp("notebook-overlap");

  function run(): void {
    overlap.reset();
    void overlap.run({});
  }

  const body: ReactNode =
    overlap.result !== null ? (
      <OpBody
        result={overlap.result}
        ok={overlap.result.ok && overlap.result.kind !== "unknown"}
        retry={run}
        opId="notebook-overlap"
        failure={(failed) => <ErrorPanel result={failed} onRetry={run} opId="notebook-overlap" />}
      >
        <ReceiptCard result={overlap.result} />
      </OpBody>
    ) : null;

  return (
    <Card title={strings.codebookOverlapTitle}>
      <Typography.Paragraph type="secondary">{strings.codebookOverlapBody}</Typography.Paragraph>
      <div className="ui-actions">
        <Button loading={overlap.running} onClick={run}>
          {strings.runnerRunLabel}
        </Button>
      </div>
      {body}
    </Card>
  );
}
