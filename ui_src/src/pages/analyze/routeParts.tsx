import { Button, Collapse, Typography } from "antd";
import type { ReactNode } from "react";
import StatusBanner from "../../components/StatusBanner";
import { getOp } from "../../api/ops";
import { strings } from "../../strings";
import {
  manifestOpId,
  routeNote,
  routeScores,
  routeSuggestions,
  textOf,
  type RouteEnvelope,
  type RouteRanked,
} from "./shapes";

// The question card's render parts (route program W3). Pure functions
// over one route envelope; the card owns state and the op calls. Split
// out of QuestionRouteCard.tsx for the 250-line rule.

/** The register listing one unconstrained select-method call returns. */
export interface RegisterRow {
  id: string;
  name: string;
}

/** The unconstrained listing's task body (a register walk, not a query). */
export const REGISTER_TASK = "List every registered method.";

/** Status banner kind per envelope status; unknown statuses read as info. */
export const STATUS_KIND: Record<string, "ok" | "warn" | "info"> = {
  resolved: "ok",
  ambiguous: "warn",
  degraded: "info",
};

/** The status word per envelope status; unknown statuses read as info. */
export const STATUS_WORD: Record<string, string> = {
  resolved: strings.routeStatusResolved,
  ambiguous: strings.routeStatusAmbiguous,
  degraded: strings.routeStatusDegraded,
};

/** One ranked row's action: hand the op to the runner, or say why not. */
export function runCell(row: RouteRanked, onRunMethod: (opId: string) => void): ReactNode {
  if (row.run_hint.op === null) {
    return <Typography.Text type="secondary">{strings.analyzeHumanOnly}</Typography.Text>;
  }
  const opId = manifestOpId(row.run_hint.op);
  if (getOp(opId) === undefined) {
    return <Typography.Text type="secondary">{strings.analyzeMethodNotWired}</Typography.Text>;
  }
  return (
    <Button size="small" type="primary" onClick={() => onRunMethod(opId)}>
      {strings.analyzeRunMethod}
    </Button>
  );
}

/** One ranked row: the method, the why, and its action. */
export function rankedRow(row: RouteRanked, onRunMethod: (opId: string) => void): ReactNode {
  return (
    <div key={row.method_id} className="ui-stack">
      <div className="ui-actions">
        <div>
          <div>
            {row.method_id} — {row.name}
          </div>
          <Typography.Text type="secondary">{row.why}</Typography.Text>
        </div>
        {runCell(row, onRunMethod)}
      </div>
    </div>
  );
}

/** The notes the intent carries: degraded mode, or an analyst override. */
export function noteLines(envelope: RouteEnvelope): ReactNode {
  const note = routeNote(envelope);
  const lines: ReactNode[] = [];
  if (note === "degraded") {
    lines.push(
      <StatusBanner key="degraded" kind="info">
        {strings.routeDegradedNote}
      </StatusBanner>,
    );
  }
  if (note === "expert_override") {
    lines.push(
      <StatusBanner key="override" kind="info">
        {strings.routeOverrideNote}
      </StatusBanner>,
    );
  }
  return lines;
}

/**
 * The ambiguity ladder as a single-choice picker (routing W4). One radio
 * per reading (the labelled group carries the pick state); "Use this
 * reading" re-routes with the picked method. Confirm-then-run: the pick
 * alone sends nothing.
 */
export function readingsPicker(
  envelope: RouteEnvelope,
  picked: string | undefined,
  onPick: (methodId: string) => void,
  onUse: () => void,
): ReactNode {
  const readings = envelope.interpretations ?? [];
  if (readings.length === 0) {
    return null;
  }
  return (
    <div className="ui-stack">
      <strong>{strings.routeReadingsTitle}</strong>
      <Typography.Text type="secondary">{strings.routeReadingsHint}</Typography.Text>
      <Typography.Paragraph className="ui-flush" type="secondary" id="route-readings-label">
        {strings.routeReadingsPickLabel}
      </Typography.Paragraph>
      <div role="radiogroup" aria-labelledby="route-readings-label" className="ui-stack">
        {readings.map((reading) => (
          <div key={reading.method_id} className="ui-stack">
            <Button
              size="small"
              role="radio"
              aria-checked={picked === reading.method_id}
              aria-label={`${reading.method_id} ${reading.method_name}`}
              type={picked === reading.method_id ? "primary" : "default"}
              onClick={() => onPick(reading.method_id)}
            >
              {reading.method_id} — {reading.method_name}
            </Button>
            <Typography.Text type="secondary">{reading.why}</Typography.Text>
          </div>
        ))}
      </div>
      <div>
        <Button size="small" type="primary" disabled={picked === undefined} onClick={onUse}>
          {strings.routeUseReading}
        </Button>
      </div>
    </div>
  );
}

/** The chosen method's next questions as chips that fill the question
    box. A chip never sends; the analyst still presses Route. */
export function suggestionChips(
  envelope: RouteEnvelope,
  onPickQuestion: (question: string) => void,
): ReactNode {
  const suggestions = routeSuggestions(envelope);
  if (suggestions.length === 0) {
    return null;
  }
  return (
    <div className="ui-stack">
      <strong>{strings.routeSuggestionsTitle}</strong>
      <Typography.Text type="secondary">{strings.routeSuggestionsHint}</Typography.Text>
      <div className="ui-actions">
        {suggestions.map((question) => (
          <Button key={question} size="small" onClick={() => onPickQuestion(question)}>
            {question}
          </Button>
        ))}
      </div>
    </div>
  );
}

/** The collapsed trace: one summary line, scores on expand. */
export function tracePanel(envelope: RouteEnvelope): ReactNode {
  const trace = envelope.trace;
  const summary = strings.routeTraceSummary
    .replace("{mode}", textOf(trace?.mode))
    .replace("{threshold}", String(trace?.threshold ?? ""))
    .replace("{margin}", String(trace?.margin ?? ""));
  const scores = routeScores(trace);
  return (
    <Collapse
      size="small"
      items={[
        {
          key: "trace",
          label: summary,
          children:
            scores.length === 0 ? (
              <Typography.Text type="secondary">{strings.routeScoresEmpty}</Typography.Text>
            ) : (
              <div className="ui-stack">
                {scores.map((row) => (
                  <div key={row.method_id} className="ui-actions">
                    <span>{row.method_id}</span>
                    <Typography.Text type="secondary">{String(row.score)}</Typography.Text>
                  </div>
                ))}
              </div>
            ),
        },
      ]}
    />
  );
}
