import { Button, Card, Input, Table, Tabs, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { dateText } from "../../api/format";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { isStatementsEnvelope, type StatementRow } from "./derivedShapes";

const STATEMENT_COLUMNS: ColumnsType<StatementRow> = [
  {
    title: strings.analyzeStatementsColumnText,
    dataIndex: "text",
    key: "text",
    render: (value: string) => <span className="ui-citation-text">{value}</span>,
  },
  {
    title: strings.analyzeStatementsColumnTemplate,
    dataIndex: "template_id",
    key: "template_id",
    render: (value: string) => <span className="ui-row-id">{value}</span>,
  },
  {
    title: strings.analyzeStatementsColumnObserved,
    dataIndex: "observed_at",
    key: "observed_at",
    render: (value: string | null) => (
      <span className="ui-num">{value === null ? "" : dateText(value)}</span>
    ),
  },
  {
    title: strings.analyzeStatementsColumnState,
    dataIndex: "superseded_by",
    key: "superseded_by",
    render: (value: string | null) =>
      value === null ? (
        <Tag>{strings.analyzeStatementsCurrent}</Tag>
      ) : (
        <Tag>{strings.analyzeStatementsSuperseded}</Tag>
      ),
  },
];

const TIMELINE_COLUMNS: ColumnsType<StatementRow> = [
  {
    title: strings.analyzeStatementsColumnText,
    dataIndex: "text",
    key: "text",
    render: (value: string) => <span className="ui-citation-text">{value}</span>,
  },
  {
    title: strings.analyzeStatementsColumnValid,
    dataIndex: "valid_at",
    key: "valid_at",
    render: (value: string | null) => (
      <span className="ui-num">{value === null ? "" : dateText(value)}</span>
    ),
  },
  {
    title: strings.analyzeStatementsColumnTemplate,
    dataIndex: "template_id",
    key: "template_id",
    render: (value: string) => <span className="ui-row-id">{value}</span>,
  },
];

/** One statement list read (POST /api/statements/list): newest first. */
function ListPane() {
  const list = useOp("statements-list");
  const { run, reset } = list;
  const load = useCallback((): void => {
    reset();
    void run({ limit: 50 });
  }, [run, reset]);
  useEffect(() => {
    load();
  }, [load]);
  const result = list.result;
  const ok = result !== null && isStatementsEnvelope(result.data);
  return (
    <OpBody result={result} ok={ok} retry={load} opId="statements-list">
      {ok && result !== null && isStatementsEnvelope(result.data) ? (
        result.data.statements.length === 0 ? (
          <StatusBanner kind="info">{strings.analyzeStatementsEmpty}</StatusBanner>
        ) : (
          <div className="ui-stack">
            <Table<StatementRow>
              size="small"
              rowKey="statement_id"
              pagination={false}
              dataSource={result.data.statements}
              columns={STATEMENT_COLUMNS}
            />
            <div className="ui-actions">
              <Button onClick={load}>{strings.analyzeRefresh}</Button>
            </div>
          </div>
        )
      ) : null}
    </OpBody>
  );
}

/** One timeline read (POST /api/statements/timeline): text query first. */
function TimelinePane() {
  const [query, setQuery] = useState("");
  const timeline = useOp("statements-timeline");
  const { run, reset } = timeline;
  const load = useCallback((): void => {
    reset();
    void run({ text_query: query, limit: 200 });
  }, [run, reset, query]);
  const result = timeline.result;
  const ok = result !== null && isStatementsEnvelope(result.data);
  return (
    <div className="ui-stack">
      <div className="ui-actions">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={strings.analyzeStatementsQueryLabel}
          aria-label={strings.analyzeStatementsQueryLabel}
        />
        <Button type="primary" onClick={load}>
          {strings.analyzeStatementsTimelineRun}
        </Button>
      </div>
      <OpBody result={result} ok={ok} retry={load} opId="statements-timeline">
        {ok && result !== null && isStatementsEnvelope(result.data) ? (
          result.data.statements.length === 0 ? (
            <StatusBanner kind="info">{strings.analyzeStatementsTimelineEmpty}</StatusBanner>
          ) : (
            <Table<StatementRow>
              size="small"
              rowKey="statement_id"
              pagination={false}
              dataSource={result.data.statements}
              columns={TIMELINE_COLUMNS}
            />
          )
        ) : null}
      </OpBody>
    </div>
  );
}

/**
 * The statements family in one card: the newest-first list and the
 * chronological history for one text query. Statement text is retrieved
 * source text, so it renders in the serif evidence voice; ids and dates
 * stay mono.
 */
export default function StatementsCard() {
  return (
    <Card title={strings.analyzeStatementsCardTitle}>
      <Typography.Paragraph type="secondary" className="ui-flush">
        {strings.analyzeStatementsCardBody}
      </Typography.Paragraph>
      <Tabs
        items={[
          { key: "list", label: strings.analyzeStatementsTabList, children: <ListPane /> },
          { key: "timeline", label: strings.analyzeStatementsTabTimeline, children: <TimelinePane /> },
        ]}
      />
    </Card>
  );
}
