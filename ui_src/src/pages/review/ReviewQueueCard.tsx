import { Button, Card, Input, Skeleton, Typography } from "antd";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import AssignmentsTable from "./AssignmentsTable";
import OverrideForm from "./OverrideForm";
import { isAssignmentsEnvelope } from "./shapes";
import type { AssignmentRow } from "./shapes";
import { loadReviewer, saveReviewer } from "./reviewerStorage";

/** Accept records the model answer as confirmed; override replaces it. */
type Decision = "accept" | "override";

/**
 * The coding review queue: one assignments read, then a human decision
 * per row. The reviewer name is remembered; an override needs a note.
 */
export default function ReviewQueueCard() {
  const navigate = useNavigate();
  const [reviewer, setReviewer] = useState(loadReviewer);
  const [overriding, setOverriding] = useState<AssignmentRow | null>(null);
  const [noteWarning, setNoteWarning] = useState("");
  const [decided, setDecided] = useState(false);

  const assignments = useOp("coding-assignments");
  const { run: assignmentsRun } = assignments;
  // One manifest row per decision kind (both post /api/review/execute);
  // the body's decision value stays the server's contract either way.
  const accept = useOp("review-accept");
  const overrideOp = useOp("review-override");

  // One GET on mount. useOp's run holds a stable identity, so the
  // [assignmentsRun] dependency never re-fires the effect.
  useEffect(() => {
    void assignmentsRun();
  }, [assignmentsRun]);

  function refresh(): void {
    assignments.reset();
    void assignments.run();
  }

  function pickReviewer(name: string): void {
    setReviewer(name);
    saveReviewer(name);
  }

  async function send(row: AssignmentRow, kind: Decision, noteText: string): Promise<void> {
    if (reviewer.trim() === "") {
      setDecided(false);
      setNoteWarning(strings.reviewReviewerRequired);
      return;
    }
    // The server reviews the AGGREGATE (a UUID), not the int row id
    // (research/review_surface.py execute_review).
    const hook = kind === "accept" ? accept : overrideOp;
    hook.reset();
    setNoteWarning("");
    const res = await hook.run({
      assignment_id: row.assignment_aggregate_id,
      decision: kind,
      reviewer: reviewer.trim(),
      note: noteText,
    });
    if (res.ok) {
      setDecided(true);
      refresh();
    }
  }

  function openOverride(row: AssignmentRow): void {
    setOverriding(row);
    setNoteWarning("");
  }

  async function confirmOverride(noteText: string): Promise<void> {
    if (overriding === null) return;
    if (noteText === "") {
      setNoteWarning(strings.reviewNoteRequired);
      return;
    }
    const row = overriding;
    setOverriding(null);
    await send(row, "override", noteText);
  }

  let body: ReactNode;
  const result = assignments.result;
  if (result === null) {
    body = <Skeleton active title={false} paragraph={{ rows: 4 }} />;
  } else if (result.kind === "network") {
    body = <ErrorPanel result={result} onRetry={refresh} opId="coding-assignments" />;
  } else if (result.kind === "unavailable") {
    body = <UnavailablePanel result={result} onRetry={refresh} />;
  } else if (!isAssignmentsEnvelope(result.data)) {
    body = (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={result.data} />
      </div>
    );
  } else {
    const rows = result.data.result;
    body = (
      <div className="ui-stack">
        {rows.length === 0 ? (
          <div className="ui-stack">
            <StatusBanner kind="info">{strings.reviewQueueEmpty}</StatusBanner>
            <div className="ui-actions">
              <Button type="primary" onClick={() => navigate("/ask")}>
                {strings.reviewQueueNext}
              </Button>
            </div>
          </div>
        ) : (
          <AssignmentsTable
            rows={rows}
            onAccept={(row) => void send(row, "accept", "")}
            onOverride={openOverride}
          />
        )}
        {decided && <StatusBanner kind="ok">{strings.reviewDecisionDone}</StatusBanner>}
        {accept.result !== null && !accept.result.ok && (
          <ErrorPanel result={accept.result} onRetry={refresh} opId="review-accept" />
        )}
        {overrideOp.result !== null && !overrideOp.result.ok && (
          <ErrorPanel result={overrideOp.result} onRetry={refresh} opId="review-override" />
        )}
      </div>
    );
  }

  return (
    <Card title={strings.reviewQueueTitle}>
      <Typography.Paragraph type="secondary">{strings.reviewQueueBody}</Typography.Paragraph>
      <div className="ui-stack">
        <div>
          <Typography.Text>{strings.reviewReviewerLabel}: </Typography.Text>
          <Input
            className="ui-narrow--name"
            value={reviewer}
            onChange={(event) => pickReviewer(event.target.value)}
            placeholder={strings.reviewReviewerLabel}
          />
          <Typography.Paragraph type="secondary" className="ui-flush">
            {strings.reviewReviewerHint}
          </Typography.Paragraph>
        </div>
        {noteWarning !== "" && <StatusBanner kind="warn">{noteWarning}</StatusBanner>}
        {body}
        {overriding !== null && (
          <OverrideForm
            onSubmit={(note) => void confirmOverride(note)}
            onCancel={() => setOverriding(null)}
          />
        )}
      </div>
    </Card>
  );
}
