import { Button, Input, Typography } from "antd";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings/analyze";
import { isAdvisorEnvelope, isRouteEnvelope } from "./shapes";
import {
  REGISTER_TASK,
  STATUS_KIND,
  STATUS_WORD,
  noteLines,
  rankedRow,
  readingsPicker,
  suggestionChips,
  tracePanel,
  type RegisterRow,
} from "./routeParts";

/**
 * The question card: one plain-language question in, the routed method
 * with its one-line why out. Ambiguous answers offer a single-choice
 * picker over their readings; using one re-routes with that method. A
 * decided answer suggests next questions; a chip fills the question box
 * and the analyst still presses Route. The analyst can also override
 * with any register method. The SPA never re-routes on its own; the
 * server's answer renders as-is.
 */
export default function QuestionRouteCard({
  onRunMethod,
  active = true,
}: {
  onRunMethod: (opId: string) => void;
  /** True while the card is the advisor's active mode; gates the one
      register listing (a hidden card must not spend the call). */
  active?: boolean;
}) {
  const [question, setQuestion] = useState("");
  const [hint, setHint] = useState("");
  const [methods, setMethods] = useState<RegisterRow[]>([]);
  const [override, setOverride] = useState<string | undefined>(undefined);
  const [picked, setPicked] = useState<string | undefined>(undefined);
  const route = useOp("analysis-route");
  const { run: runRegister } = useOp("analysis-select-method");

  // The override picker's source: one unconstrained register listing,
  // loaded when the card becomes the active mode. runRegister holds a
  // stable identity (the useOp contract), so this fires once per entry.
  useEffect(() => {
    if (!active) {
      return;
    }
    let alive = true;
    void (async () => {
      const result = await runRegister({ task_description: REGISTER_TASK });
      if (!alive) return;
      if (result.ok && isAdvisorEnvelope(result.data)) {
        setMethods(
          result.data.recommendations.map((row) => ({ id: row.id, name: row.name })),
        );
      }
    })();
    return () => {
      alive = false;
    };
  }, [active, runRegister]);

  async function routeWith(methodId?: string): Promise<void> {
    if (question.trim() === "") {
      setHint(strings.routeQuestionRequired);
      return;
    }
    setHint("");
    // The highlights follow the answer: a plain re-route drops any
    // picked override and any stale reading pick, and a guard-tripped
    // click (empty question) never lights one up.
    setOverride(methodId);
    setPicked(undefined);
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
          {readingsPicker(envelope, picked, setPicked, () => void routeWith(picked))}
          {suggestionChips(envelope, setQuestion)}
          {tracePanel(envelope)}
          <div className="ui-stack">
            <Typography.Paragraph
              className="ui-flush"
              type="secondary"
              id="route-override-label"
            >
              {strings.routeOverrideLabel}
            </Typography.Paragraph>
            {/* The Analyze runner's picker idiom: one aria-pressed button
                per register method; clicking re-routes with that id. The
                group + labels let screen-reader users hear the names. */}
            <div role="group" aria-labelledby="route-override-label" className="ui-actions">
              {methods.map((row) => (
                <Button
                  key={row.id}
                  size="small"
                  aria-label={`${row.id} ${row.name}`}
                  aria-pressed={override === row.id}
                  type={override === row.id ? "primary" : "default"}
                  onClick={() => void routeWith(row.id)}
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
      <Typography.Paragraph type="secondary">{strings.routeIntro}</Typography.Paragraph>
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
