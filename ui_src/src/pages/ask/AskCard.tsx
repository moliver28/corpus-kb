import { Button, Card, Skeleton, Typography } from "antd";
import type { ReactNode } from "react";
import { useRef, useState } from "react";
import CitedAnswer from "../../components/CitedAnswer";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import LabeledField, { useFirstErrorFocus } from "../../components/LabeledField";
import LongOpControls from "../../components/LongOpControls";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { getOp } from "../../api/ops";
import { callOpStream } from "../../api/stream";
import { useLongOp } from "../../hooks/useLongOp";
import { strings } from "../../strings";
import { loadHistory, saveHistory, type HistoryEntry } from "./askHistory";
import EvidenceList from "./EvidenceList";
import {
  askEvidence,
  excludedCount,
  isAskEnvelope,
  noteOf,
  toCitations,
  verifyOf,
} from "./shapes";

/**
 * The ask action: one question, one cited answer. The wait runs through
 * useLongOp (elapsed time, stop waiting, honest unknown outcome), and
 * the honest empty/degraded states use the exact program strings.
 * The answer streams: grounded sentences land as they complete, and the
 * settle receipt is the full envelope (citations, verify, sources), the
 * same JSON envelope the non-streaming call returns. The question
 * history is client-only sessionStorage.
 */
export default function AskCard() {
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory);
  const { rootRef, flag } = useFirstErrorFocus();
  // The streamed answer so far (grounded sentences only). The settled
  // view below replaces it once the envelope lands.
  const [streamText, setStreamText] = useState("");
  // The long-op closure reads the question from a ref: the value at
  // start time, not at render time.
  const queryRef = useRef("");
  const ask = useLongOp("ask", (signal) => {
    const op = getOp("ask");
    return callOpStream(op?.path ?? "", {
      method: "POST",
      body: { query: queryRef.current, stream: true },
      signal,
      timeoutMs: "none",
      onDelta: (piece) => setStreamText((prev) => prev + piece),
    });
  });

  // The USER's cancel intent: the settled long-op result still carries
  // the server envelope, so the history guard reads the intent ref, not
  // a possibly stale hook state.
  const cancelRequestedRef = useRef(false);

  async function run(): Promise<void> {
    if (ask.running) {
      // One flight at a time: a second submit must not duplicate history.
      return;
    }
    if (query.trim() === "") {
      setError(strings.askQueryRequired);
      flag();
      return;
    }
    setError("");
    queryRef.current = query.trim();
    cancelRequestedRef.current = false;
    setStreamText("");
    const res = await ask.start();
    if (res.ok && !cancelRequestedRef.current) {
      const next = [
        { question: queryRef.current, at: new Date().toISOString() },
        ...history,
      ].slice(0, 10);
      setHistory(next);
      saveHistory(next);
    }
  }

  function stopWaiting(): void {
    cancelRequestedRef.current = true;
    ask.cancel();
  }

  function clearHistory(): void {
    setHistory([]);
    saveHistory([]);
  }

  function retry(): void {
    void run();
  }

  let resultView: ReactNode = null;
  const settled = ask.result;
  if (settled !== null) {
    if (settled.kind === "unavailable") {
      resultView = <UnavailablePanel result={settled} onRetry={retry} />;
    } else if (settled.ok && isAskEnvelope(settled.data)) {
      const env = settled.data;
      const excluded = excludedCount(env.degraded_excluded);
      if (env.status === "success") {
        resultView = (
          <div className="ui-stack">
            <CitedAnswer
              answer={env.result.answer}
              citations={toCitations(env)}
              verify={verifyOf(env)}
            />
            {noteOf(env) !== "" && (
              <StatusBanner kind="info">
                {strings.askNoteLabel}: {noteOf(env)}
              </StatusBanner>
            )}
            {excluded > 0 && (
              <StatusBanner kind="warn">
                {strings.askDegradedExcluded.replace("{count}", String(excluded))}
              </StatusBanner>
            )}
          </div>
        );
      } else if (env.status === "empty") {
        resultView = (
          <div className="ui-stack">
            <StatusBanner kind="warn">{strings.askEmptyState}</StatusBanner>
            {noteOf(env) !== "" && (
              <StatusBanner kind="info">
                {strings.askNoteLabel}: {noteOf(env)}
              </StatusBanner>
            )}
          </div>
        );
      } else if (env.status === "degraded") {
        resultView = (
          <div className="ui-stack">
            <StatusBanner kind="warn">{strings.askDegradedState}</StatusBanner>
            <EvidenceList items={askEvidence(env)} />
            {noteOf(env) !== "" && (
              <StatusBanner kind="info">
                {strings.askNoteLabel}: {noteOf(env)}
              </StatusBanner>
            )}
            {excluded > 0 && (
              <StatusBanner kind="warn">
                {strings.askDegradedExcluded.replace("{count}", String(excluded))}
              </StatusBanner>
            )}
          </div>
        );
      } else {
        resultView = (
          <div>
            <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
            <JsonViewer value={settled.data} />
          </div>
        );
      }
    } else if (settled.ok) {
      // No status word or a drifted shape is not a receipt.
      resultView = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={settled.data} />
        </div>
      );
    } else {
      resultView = <ErrorPanel result={settled} onRetry={retry} opId="ask" />;
    }
  }

  return (
    <Card title={strings.askTitle}>
      <Typography.Paragraph type="secondary">{strings.askBody}</Typography.Paragraph>
      <div className="ui-actions" ref={rootRef}>
        <LabeledField
          label={strings.askQueryLabel}
          value={query}
          onChange={setQuery}
          error={error}
          required
          name="query"
          widthClass="ui-narrow"
          onPressEnter={() => void run()}
        />
        <Button type="primary" loading={ask.running} onClick={() => void run()}>
          {strings.askAction}
        </Button>
      </div>
      {ask.running && streamText === "" && (
        <Skeleton active title={false} paragraph={{ rows: 3 }} />
      )}
      {ask.running && streamText !== "" && (
        <div className="ui-stack">
          <Card title={strings.citedAnswerTitle}>
            <p className="ui-answer">{streamText}</p>
          </Card>
          <StatusBanner kind="info">{strings.askStreamingNote}</StatusBanner>
        </div>
      )}
      <LongOpControls op={ask} onRetry={retry} onCancel={stopWaiting} />
      {resultView}
      <div className="ui-heading-row">
        <h2 className="ui-heading">{strings.askHistoryTitle}</h2>
        {history.length > 0 && (
          <Button type="text" size="small" onClick={clearHistory}>
            {strings.askHistoryClear}
          </Button>
        )}
      </div>
      {history.length === 0 ? (
        <Typography.Text type="secondary">{strings.askHistoryEmpty}</Typography.Text>
      ) : (
        <ul className="ui-history">
          {history.map((entry) => (
            <li key={entry.at}>
              <button
                type="button"
                className="ui-history__item"
                onClick={() => setQuery(entry.question)}
              >
                {entry.question}
              </button>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
