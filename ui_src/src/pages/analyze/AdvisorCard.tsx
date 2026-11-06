import { Button, Card, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import LabeledField, { useFirstErrorFocus } from "../../components/LabeledField";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { getOp } from "../../api/ops";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import QuestionRouteCard from "./QuestionRouteCard";
import {
  isAdvisorEnvelope,
  manifestOpId,
  textOf,
  type AdvisorRow,
} from "./shapes";

interface Row {
  key: string;
  row: AdvisorRow;
  runnable: boolean;
}

/** The method cell: the name with its summary right under it. */
function methodCell(record: Row): ReactNode {
  return (
    <div>
      <div>{record.row.name}</div>
      <Typography.Text type="secondary">{record.row.summary}</Typography.Text>
    </div>
  );
}

/** One row's action cell: hand the method to the runner, or say why not. */
function runCell(record: Row, onRunMethod: (opId: string) => void): ReactNode {
  if (record.row.op === null) {
    return <Typography.Text type="secondary">{strings.analyzeHumanOnly}</Typography.Text>;
  }
  if (!record.runnable) {
    return <Typography.Text type="secondary">{strings.analyzeMethodNotWired}</Typography.Text>;
  }
  return (
    <Button
      size="small"
      type="primary"
      onClick={() => onRunMethod(manifestOpId(record.row.op ?? ""))}
    >
      {strings.analyzeRunMethod}
    </Button>
  );
}

/** The expanded row: the register's own validation and claims guidance. */
function guidanceRow(record: Row): ReactNode {
  return (
    <div className="ui-stack">
      <Typography.Paragraph className="ui-flush">
        <strong>{strings.analyzeValidationLabel}: </strong>
        {textOf(record.row.validation_required)}
      </Typography.Paragraph>
      <Typography.Paragraph className="ui-flush">
        <strong>{strings.analyzeClaimsLabel}: </strong>
        {textOf(record.row.permitted_claims)}
      </Typography.Paragraph>
      <Typography.Paragraph className="ui-flush" type="secondary">
        <strong>{strings.analyzeDoNotClaimLabel}: </strong>
        {textOf(record.row.do_not_claim)}
      </Typography.Paragraph>
    </div>
  );
}

/**
 * The method advisor: one select-method call, the register's ranking
 * with validation and permitted claims, and a per-row action that hands
 * the method to the op runner. The SPA never re-ranks; the rows render
 * in the server's order. The mode toggle switches between the task
 * form and the plain-language question router (routing program W3).
 */
export default function AdvisorCard({ onRunMethod }: { onRunMethod: (opId: string) => void }) {
  const [task, setTask] = useState("");
  const [error, setError] = useState("");
  const { rootRef, flag } = useFirstErrorFocus();
  const [mode, setMode] = useState<"task" | "question">("task");
  const advisor = useOp("analysis-select-method");

  async function run(): Promise<void> {
    if (task.trim() === "") {
      setError(strings.analyzeAdvisorTaskRequired);
      flag();
      return;
    }
    setError("");
    advisor.reset();
    await advisor.run({ task_description: task.trim() });
  }

  let body: ReactNode = null;
  const result = advisor.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={() => void run()} />;
    } else if (result.ok && isAdvisorEnvelope(result.data)) {
      const rows: Row[] = result.data.recommendations.map((row) => ({
        key: String(row.rank),
        row,
        runnable: row.op !== null && getOp(manifestOpId(row.op)) !== undefined,
      }));
      const columns: ColumnsType<Row> = [
        { title: strings.analyzeRankColumn, dataIndex: ["row", "rank"], key: "rank", width: 60 },
        { title: strings.analyzeMethodColumn, key: "name", render: (_v, r) => methodCell(r) },
        { title: strings.analyzeWhyColumn, dataIndex: ["row", "rank_reason"], key: "why" },
        { title: "", key: "run", render: (_v, r) => runCell(r, onRunMethod) },
      ];
      body = (
        <div className="ui-stack">
          {rows.length === 0 && (
            <StatusBanner kind="info">{strings.analyzeAdvisorEmpty}</StatusBanner>
          )}
          <Table<Row>
            size="small"
            rowKey="key"
            pagination={false}
            dataSource={rows}
            columns={columns}
            expandable={{
              defaultExpandAllRows: true,
              expandedRowRender: guidanceRow,
              expandRowByClick: false,
            }}
          />
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
      body = (
        <ErrorPanel result={result} onRetry={() => void run()} opId="analysis-select-method" />
      );
    }
  }

  return (
    <Card title={strings.analyzeAdvisorTitle}>
      <Typography.Paragraph type="secondary">{strings.analyzeAdvisorBody}</Typography.Paragraph>
      <div className="ui-actions">
        <Button
          aria-pressed={mode === "task"}
          type={mode === "task" ? "primary" : "default"}
          onClick={() => setMode("task")}
        >
          {strings.routeModeTaskForm}
        </Button>
        <Button
          aria-pressed={mode === "question"}
          type={mode === "question" ? "primary" : "default"}
          onClick={() => setMode("question")}
        >
          {strings.routeModeQuestion}
        </Button>
      </div>
      {/* Both branches stay mounted (hidden, not unmounted), so the
          task text and the question text survive mode switches the same
          way. Testing Library skips hidden subtrees in role queries. */}
      <div hidden={mode !== "task"}>
        <div className="ui-actions" ref={rootRef}>
          <LabeledField
            label={strings.analyzeAdvisorTaskLabel}
            value={task}
            onChange={setTask}
            error={error}
            required
            name="task_description"
            widthClass="ui-narrow"
            onPressEnter={() => void run()}
          />
          <Button type="primary" loading={advisor.running} onClick={() => void run()}>
            {strings.analyzeAdvisorAction}
          </Button>
        </div>
        {body}
      </div>
      <div hidden={mode !== "question"}>
        <QuestionRouteCard onRunMethod={onRunMethod} active={mode === "question"} />
      </div>
    </Card>
  );
}
