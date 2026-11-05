import { Button, Input, Typography } from "antd";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { isRouteEnvelope } from "./shapes";
import {
  REGISTER_TASK,
  STATUS_KIND,
  STATUS_WORD,
  noteLines,
  rankedRow,
  tracePanel,
  type RegisterRow,
} from "./routeParts";

/**
 * The question card: one plain-language question in, the routed method
 * with its one-line why out. Ambiguous answers list their readings
 * (read-only in this wave); the analyst can override with any register
 * method. The SPA never re-routes; the server's answer renders as-is.
 */
export default function QuestionRouteCard({ onRunMethod }: { onRunMethod: (opId: string) => void }) {
  const [question, setQuestion] = useState("");
  const [hint, setHint] = useState("");
  const [methods, setMethods] = useState<RegisterRow[]>([]);
  const [override, setOverride] = useState<string | undefined>(undefined);
  const route = useOp("analysis-route");
  const { run: runRegister } = useOp("analysis-select-method");

  // The override select's source: one unconstrained register listing,
  // loaded once per card mount (entering question mode). runRegister
  // holds a stable identity (the useOp contract), so this fires once.
  useEffect(() => {
    let alive = true;
    void (async () => {
      const result = await runRegister({ task_description: REGISTER_TASK });
      if (!alive) return;
      if (result.ok && result.data !== null && typeof result.data === "object") {
        const rows = (result.data as { recommendations?: unknown }).recommendations;
        if (Array.isArray(rows)) {
          setMethods(
            rows
              .map((row) => row as Record<string, unknown>)
              .filter((row) => typeof row.id === "string" && typeof row.name === "string")
              .map((row) => ({ id: row.id as string, name: row.name as string })),
          );
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [runRegister]);

  async function routeWith(methodId?: string): Promise<void> {
    if (question.trim() === "") {
      setHint(strings.routeQuestionRequired);
      return;
    }
    setHint("");
    route.reset();
    const body: Record<string, unknown> = { question: question.trim() };
    if (methodId !== undefined) {
      body.method_id = methodId;
    }
    await route.run(body);
  }

  let body: ReactNode = null;
  const result = route.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={() => void routeWith()} />;
    } else if (result.ok && isRouteEnvelope(result.data)) {
      const envelope = result.data;
      const top = envelope.ranked[0];
      body = (
        <div className="ui-stack">
          <StatusBanner kind={STATUS_KIND[envelope.status] ?? "info"}>
            {STATUS_WORD[envelope.status] ?? envelope.status}
          </StatusBanner>
          {noteLines(envelope)}
          {envelope.ranked.length === 0 && (
            <StatusBanner kind="info">{strings.routeEmpty}</StatusBanner>
          )}
          {top !== undefined && (
            <div>
              <strong>{strings.routeTopWhyLabel}: </strong>
              {top.why}
            </div>
          )}
          {envelope.ranked.map((row) => rankedRow(row, onRunMethod))}
          {envelope.interpretations !== undefined && envelope.interpretations.length > 0 && (
            <div className="ui-stack">
              <strong>{strings.routeReadingsTitle}</strong>
              <Typography.Text type="secondary">{strings.routeReadingsHint}</Typography.Text>
              {envelope.interpretations.map((reading) => (
                <div key={reading.method_id}>
                  <div>
                    {reading.method_id} — {reading.method_name}
                  </div>
                  <Typography.Text type="secondary">{reading.why}</Typography.Text>
                </div>
              ))}
            </div>
          )}
          {tracePanel(envelope)}
          <div className="ui-stack">
            <Typography.Paragraph className="ui-flush" type="secondary">
              {strings.routeOverrideLabel}
            </Typography.Paragraph>
            {/* The Analyze runner's picker idiom: one aria-pressed button
                per register id; clicking re-routes with that method_id. */}
            <div className="ui-actions">
              {methods.map((row) => (
                <Button
                  key={row.id}
                  size="small"
                  aria-pressed={override === row.id}
                  type={override === row.id ? "primary" : "default"}
                  onClick={() => {
                    setOverride(row.id);
                    void routeWith(row.id);
                  }}
                >
                  {row.id}
                </Button>
              ))}
            </div>
          </div>
        </div>
      );
    } else if (result.ok) {
      body = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={result.data} />
        </div>
      );
    } else {
      body = <ErrorPanel result={result} onRetry={() => void routeWith()} opId="analysis-route" />;
    }
  }

  return (
    <div className="ui-stack">
      <Typography.Paragraph type="secondary">{strings.routeModeQuestion}</Typography.Paragraph>
      <div className="ui-actions">
        <Input.TextArea
          aria-label={strings.routeQuestionLabel}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={strings.routeQuestionPlaceholder}
          autoSize={{ minRows: 2, maxRows: 5 }}
        />
        <Button type="primary" loading={route.running} onClick={() => void routeWith()}>
          {strings.routeQuestionAction}
        </Button>
      </div>
      {hint !== "" && <StatusBanner kind="warn">{hint}</StatusBanner>}
      {body}
    </div>
  );
}
