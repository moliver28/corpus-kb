import { Button, Typography } from "antd";
import { useEffect, useState } from "react";
import type { OpResult } from "../../api/types";
import ErrorPanel from "../../components/ErrorPanel";
import LabeledField from "../../components/LabeledField";
import OpBody from "../../components/OpBody";
import ReceiptCard from "../../components/ReceiptCard";
import { useOp } from "../../hooks/useOp";
import { useRowRead } from "../../hooks/useRowRead";
import { strings } from "../../strings/review";
import { isChunksEnvelope } from "./shapes";

/** Chunk rows asked for in one read; one extra row answers "more exist". */
const CHUNK_WINDOW = 50;

/**
 * The manifest method ops that take chunk_ids (the panel's picker).
 * Exported for the manifest classification test: NO picker op is
 * longRunning today, so RunPane's plain run path is the whole story.
 * When a long op joins the picker, bring the long-run path back
 * (useLongOp + LongOpControls, the OpRunner pattern).
 */
export const METHOD_OPS: readonly { opId: string; label: string }[] = [
  { opId: "sentiment-screen", label: strings.analyzeOpSentiment },
  { opId: "nli-classify", label: strings.analyzeOpNli },
  { opId: "lexical-classify", label: strings.analyzeOpLexical },
  { opId: "neighbors-classify", label: strings.analyzeOpNeighbors },
  { opId: "centroids-classify", label: strings.analyzeOpCentroids },
  { opId: "keyphrases-extract", label: strings.analyzeOpKeyphrases },
];

/** Dispatch one settled run through the one honest body (OpRunner's). */
function ResultView({
  result,
  onRetry,
  opId,
}: {
  result: OpResult;
  onRetry: () => void;
  opId: string;
}) {
  return (
    <OpBody
      result={result}
      ok={result.ok && result.kind !== "unknown"}
      retry={onRetry}
      opId={opId}
      failure={(failed) => <ErrorPanel result={failed} onRetry={onRetry} opId={opId} />}
    >
      <ReceiptCard result={result} />
    </OpBody>
  );
}

/**
 * One scoped run: the picked method over the record's chunk ids. The
 * body is built here (never hand-typed JSON). key={opId} remounts this
 * pane on a method switch, so no receipt or loading state from the
 * previous op can leak into it (the OpRunner rule).
 */
function RunPane({ opId, ids }: { opId: string; ids: string[] }) {
  const simple = useOp(opId);
  const [labelsText, setLabelsText] = useState("");
  const [labelsError, setLabelsError] = useState("");

  /** The scoped body, or null when a required field failed inline. */
  function buildBody(): Record<string, unknown> | null {
    const body: Record<string, unknown> = { chunk_ids: ids };
    if (opId === "nli-classify") {
      const labels = labelsText
        .split(",")
        .map((word) => word.trim())
        .filter((word) => word !== "");
      if (labels.length === 0) {
        setLabelsError(strings.reviewAnalyzeLabelsRequired);
        return null;
      }
      setLabelsError("");
      body.labels = labels;
    }
    return body;
  }

  async function run(): Promise<void> {
    const body = buildBody();
    if (body === null) return;
    await simple.run(body);
  }

  return (
    <div className="ui-stack">
      {opId === "nli-classify" && (
        <LabeledField
          label={strings.reviewAnalyzeLabelsLabel}
          value={labelsText}
          onChange={(value) => setLabelsText(value)}
          error={labelsError}
          required
        />
      )}
      <div className="ui-actions">
        <Button type="primary" loading={simple.running} onClick={() => void run()}>
          {strings.runnerRunLabel}
        </Button>
      </div>
      {simple.result !== null && (
        <div className="ui-stack-gap-lg">
          <ResultView result={simple.result} onRetry={() => void run()} opId={opId} />
        </div>
      )}
    </div>
  );
}

/**
 * The drawer's NLP panel (Phase-2 Wave 5): the six chunk-scoped method
 * ops, pre-filled with THIS document's chunk ids from the chunks-list
 * window read, answers inline. The window asks for one row past the cap,
 * so the capped note is the sentinel, never a guess. Results are
 * inline-only — nothing persists here (the filed follow-up owns the
 * attach-and-persist scope).
 */
export default function DrawerAnalyze({ docId }: { docId: string }) {
  const { read: readChunks, state: chunksState } = useRowRead("chunks-list");
  const [opId, setOpId] = useState<string>("sentiment-screen");

  // One window read per opened document; the row tag keeps a slow answer
  // for a document the user already left from rendering here.
  useEffect(() => {
    readChunks(docId, {
      pathParams: { doc_id: docId },
      query: { limit: String(CHUNK_WINDOW + 1), offset: "0" },
    });
  }, [docId, readChunks]);

  const shown =
    chunksState !== null && chunksState.rowId === docId ? chunksState.result : null;
  const rows = shown !== null && shown.ok && isChunksEnvelope(shown.data) ? shown.data.result : null;
  const overWindow = rows !== null && rows.length > CHUNK_WINDOW;
  const ids = rows === null ? [] : rows.slice(0, CHUNK_WINDOW).map((row) => row.chunk_id);

  function retryChunks(): void {
    readChunks(docId, {
      pathParams: { doc_id: docId },
      query: { limit: String(CHUNK_WINDOW + 1), offset: "0" },
    });
  }

  return (
    <div className="ui-stack">
      <Typography.Title level={5} className="ui-flush">
        {strings.reviewAnalyzeTitle}
      </Typography.Title>
      <Typography.Paragraph type="secondary" className="ui-flush">
        {strings.reviewAnalyzeBody}
      </Typography.Paragraph>
      <OpBody
        result={shown}
        ok={rows !== null}
        retry={retryChunks}
        opId="chunks-list"
        skeletonRows={2}
      >
        {ids.length === 0 ? (
          <Typography.Text type="secondary">{strings.reviewAnalyzeNoChunks}</Typography.Text>
        ) : (
          <div className="ui-stack">
            <Typography.Text type="secondary">
              {overWindow
                ? strings.reviewAnalyzeScopeCapped.replace("{n}", String(CHUNK_WINDOW))
                : strings.reviewAnalyzeScopeAll.replace("{n}", String(ids.length))}
            </Typography.Text>
            <div className="ui-actions" role="group" aria-label={strings.reviewAnalyzePickLabel}>
              {METHOD_OPS.map((method) => (
                <Button
                  key={method.opId}
                  size="small"
                  title={method.opId}
                  aria-pressed={opId === method.opId}
                  type={opId === method.opId ? "primary" : "default"}
                  onClick={() => setOpId(method.opId)}
                >
                  {method.label}
                </Button>
              ))}
            </div>
            <RunPane key={opId} opId={opId} ids={ids} />
          </div>
        )}
      </OpBody>
    </div>
  );
}
