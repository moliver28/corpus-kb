import { Button, Card, Input, Modal, Skeleton, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import {
  confidenceText,
  isAssignmentsEnvelope,
  reviewStateWord,
  type AssignmentRow,
} from "./shapes";

// Persisted non-sensitive UI state (F10): the reviewer name only, under
// the corpus-kb: prefix.
const REVIEWER_KEY = "corpus-kb:reviewer";

function loadReviewer(): string {
  try {
    return window.localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveReviewer(name: string): void {
  try {
    window.localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    // Private mode: the name just does not persist.
  }
}

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
  const [note, setNote] = useState("");
  const [noteWarning, setNoteWarning] = useState("");
  const [decided, setDecided] = useState(false);

  const assignments = useOp("coding-assignments");
  // One manifest row per decision kind (both post /api/review/execute);
  // the body's decision value stays the server's contract either way.
  const accept = useOp("review-accept");
  const overrideOp = useOp("review-override");

  // One GET on mount through a ref (useOp's run is not stable).
  const runRef = useRef<() => Promise<unknown>>(assignments.run);
  useEffect(() => {
    void runRef.current();
  }, []);

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
    setNote("");
    setNoteWarning("");
  }

  async function confirmOverride(): Promise<void> {
    if (overriding === null) return;
    if (note.trim() === "") {
      setNoteWarning(strings.reviewNoteRequired);
      return;
    }
    const row = overriding;
    setOverriding(null);
    await send(row, "override", note.trim());
  }

  const columns: ColumnsType<AssignmentRow> = [
    { title: strings.reviewColumnCode, dataIndex: "code_id", key: "code_id" },
    { title: strings.reviewColumnUnit, dataIndex: "unit_id", key: "unit_id" },
    {
      title: strings.reviewColumnState,
      dataIndex: "status",
      key: "status",
      render: (value: unknown) => <Tag>{reviewStateWord(String(value))}</Tag>,
    },
    {
      title: strings.reviewColumnConfidence,
      key: "confidence",
      render: (_value: unknown, row: AssignmentRow) => confidenceText(row.confidence),
    },
    {
      title: strings.reviewColumnActions,
      key: "actions",
      render: (_value: unknown, row: AssignmentRow) => (
        <div className="ui-actions">
          <Button size="small" onClick={() => void send(row, "accept", "")}>
            {strings.reviewAccept}
          </Button>
          <Button size="small" onClick={() => openOverride(row)}>
            {strings.reviewOverride}
          </Button>
        </div>
      ),
    },
  ];

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
          <Table<AssignmentRow>
            size="small"
            rowKey="assignment_id"
            pagination={false}
            dataSource={rows}
            columns={columns}
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
            style={{ maxWidth: 280 }}
            value={reviewer}
            onChange={(event) => pickReviewer(event.target.value)}
            placeholder={strings.reviewReviewerLabel}
          />
          <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
            {strings.reviewReviewerHint}
          </Typography.Paragraph>
        </div>
        {noteWarning !== "" && <StatusBanner kind="warn">{noteWarning}</StatusBanner>}
        {body}
      </div>
      <Modal
        title={strings.reviewOverrideTitle}
        open={overriding !== null}
        onOk={() => void confirmOverride()}
        onCancel={() => setOverriding(null)}
        okText={strings.reviewOverride}
        cancelText={strings.confirmCancel}
      >
        <p>{strings.reviewOverrideBody}</p>
        <Input.TextArea
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder={strings.reviewNoteLabel}
          rows={3}
        />
      </Modal>
    </Card>
  );
}
