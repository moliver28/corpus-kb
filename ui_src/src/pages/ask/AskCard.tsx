import { Button, Card, Input, Skeleton, Typography } from "antd";
import type { ReactNode } from "react";
import { useRef, useState } from "react";
import CitedAnswer from "../../components/CitedAnswer";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { callOp } from "../../api/client";
import { getOp } from "../../api/ops";
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
 * The question history is client-only sessionStorage.
 */
export default function AskCard() {
  const [query, setQuery] = useState("");
  const [hint, setHint] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>(loadHistory);
  // The long-op closure reads the question from a ref: the value at
  // start time, not at render time.
  const queryRef = useRef("");
  const ask = useLongOp("ask", (signal) => {
    const op = getOp("ask");
    return callOp<unknown>(op?.path ?? "", {
      method: "POST",
      body: { query: queryRef.current },
      signal,
      timeoutMs: "none",
    });
  });

  async function run(): Promise<void> {
    if (query.trim() === "") {
      setHint(strings.askQueryRequired);
      return;
    }
    setHint("");
    queryRef.current = query.trim();
    const res = await ask.start();
    if (res.ok) {
      const next = [
        { question: queryRef.current, at: new Date().toISOString() },
        ...history,
      ].slice(0, 10);
      setHistory(next);
      saveHistory(next);
    }
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
        resultView = <StatusBanner kind="warn">{strings.askEmptyState}</StatusBanner>;
      } else if (env.status === "degraded") {
        resultView = (
          <div className="ui-stack">
            <StatusBanner kind="warn">{strings.askDegradedState}</StatusBanner>
            <EvidenceList items={askEvidence(env)} />
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
      resultView = <ErrorPanel result={settled} onRetry={retry} />;
    }
  }

  return (
    <Card title={strings.askTitle}>
      <Typography.Paragraph type="secondary">{strings.askBody}</Typography.Paragraph>
      <div className="ui-actions">
        <Input
          style={{ maxWidth: 480 }}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={strings.askQueryLabel}
          onPressEnter={() => void run()}
        />
        <Button type="primary" loading={ask.running} onClick={() => void run()}>
          {strings.askAction}
        </Button>
      </div>
      {hint !== "" && <StatusBanner kind="warn">{hint}</StatusBanner>}
      {ask.running && (
        <div className="ui-stack">
          <Skeleton active title={false} paragraph={{ rows: 3 }} />
          <div className="ui-actions">
            <StatusBanner kind="info">
              {strings.runnerRunningElapsed.replace("{sec}", String(ask.elapsedSec))}
            </StatusBanner>
            <Button onClick={() => ask.cancel()}>{strings.runnerCancelLabel}</Button>
          </div>
        </div>
      )}
      {ask.cancelled && <StatusBanner kind="warn">{strings.runnerCancelled}</StatusBanner>}
      {ask.unknownOutcome && (
        <StatusBanner kind="warn">{strings.runnerUnknownOutcome}</StatusBanner>
      )}
      {resultView}
      <Typography.Title level={5}>{strings.askHistoryTitle}</Typography.Title>
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
