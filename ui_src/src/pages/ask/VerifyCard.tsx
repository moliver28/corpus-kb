import { Button, Card, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useState } from "react";
import BadgeLine from "../../components/BadgeLine";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import LabeledField, { useFirstErrorFocus } from "../../components/LabeledField";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { scoreText } from "./shapes";
import { abstainedOf, isVerifyEnvelope, type ClaimVerdictRow } from "./verifyShapes";

const VERDICT_LABELS: Record<string, string> = {
  entailed: strings.askVerdictEntailed,
  contradicted: strings.askVerdictContradicted,
  unsupported: strings.askVerdictUnsupported,
};

/** One server verdict word -> the user-facing word (unknown words stay). */
function verdictText(label: string): string {
  return VERDICT_LABELS[label] ?? label;
}

/** The chunk-id list from the raw field (commas or whitespace between ids). */
function parseChunkIds(raw: string): string[] {
  return raw.split(/[\s,]+/).filter((id) => id !== "");
}

const VERDICT_COLUMNS: ColumnsType<ClaimVerdictRow> = [
  { title: strings.askColumnClaim, dataIndex: "claim", key: "claim" },
  {
    title: strings.askColumnVerdict,
    key: "verdict",
    render: (_value: unknown, row: ClaimVerdictRow) => verdictText(row.label),
  },
  {
    title: strings.askColumnConfidence,
    key: "confidence",
    render: (_value: unknown, row: ClaimVerdictRow) => scoreText(row.confidence),
  },
];

/**
 * The verify tab: claim-level groundedness against the cited chunks. The
 * answer and its chunk ids run through the one verify op; the badge and
 * its thresholds are the same generated policy the answer badge uses.
 */
export default function VerifyCard() {
  const [answer, setAnswer] = useState("");
  const [chunks, setChunks] = useState("");
  const [answerError, setAnswerError] = useState("");
  const [chunksError, setChunksError] = useState("");
  const { rootRef, flag } = useFirstErrorFocus();
  const verify = useOp("verify");

  async function run(): Promise<void> {
    const answerText = answer.trim();
    const chunkIds = parseChunkIds(chunks);
    if (answerText === "") {
      setAnswerError(strings.askVerifyAnswerRequired);
    } else {
      setAnswerError("");
    }
    if (chunkIds.length === 0) {
      setChunksError(strings.askVerifyChunksRequired);
    } else {
      setChunksError("");
    }
    if (answerText === "" || chunkIds.length === 0) {
      flag();
      return;
    }
    verify.reset();
    await verify.run({ answer: answerText, chunk_ids: chunkIds });
  }

  function retry(): void {
    void run();
  }

  let body: ReactNode = null;
  const result = verify.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={retry} />;
    } else if (result.ok && isVerifyEnvelope(result.data)) {
      const env = result.data;
      // No verdicts with or without the abstain word means the judge had
      // nothing to check: the honest note, never an empty table.
      if (abstainedOf(env) || env.result.verdicts.length === 0) {
        body = <StatusBanner kind="warn">{strings.askVerifyAbstained}</StatusBanner>;
      } else {
        body = (
          <div className="ui-stack">
            {/* The badge line is the answer badge's (components/BadgeLine). */}
            <BadgeLine verify={env.result} />
            <Table<ClaimVerdictRow>
              size="small"
              rowKey={(_row: ClaimVerdictRow, index?: number) => String(index ?? 0)}
              pagination={false}
              dataSource={env.result.verdicts}
              columns={VERDICT_COLUMNS}
            />
          </div>
        );
      }
    } else if (result.ok) {
      body = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={result.data} />
        </div>
      );
    } else {
      body = <ErrorPanel result={result} onRetry={retry} opId="verify" />;
    }
  }

  return (
    <Card title={strings.askTabVerify}>
      <Typography.Paragraph type="secondary">{strings.askVerifyBody}</Typography.Paragraph>
      <div className="ui-actions" ref={rootRef}>
        <LabeledField
          label={strings.askVerifyAnswerLabel}
          value={answer}
          onChange={setAnswer}
          error={answerError}
          multiline
          required
          name="answer"
          widthClass="ui-narrow"
        />
        <LabeledField
          label={strings.askVerifyChunksLabel}
          value={chunks}
          onChange={setChunks}
          error={chunksError}
          machine
          required
          name="chunk_ids"
          widthClass="ui-narrow"
          onPressEnter={() => void run()}
        />
        <Button type="primary" loading={verify.running} onClick={() => void run()}>
          {strings.askVerifyAction}
        </Button>
      </div>
      <Typography.Paragraph type="secondary">
        {strings.askVerifyChunksHint}
      </Typography.Paragraph>
      {body}
    </Card>
  );
}
