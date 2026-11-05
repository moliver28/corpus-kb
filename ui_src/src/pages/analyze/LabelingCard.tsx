import { Button, Card, Table, Tabs, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect } from "react";
import { dateText } from "../../api/format";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import LabelingGoldPanel from "./LabelingGoldPanel";
import LabelingTriagePanel from "./LabelingTriagePanel";
import { isLabelsEnvelope, isLfsEnvelope } from "./labelingShapes";
import type { LabelRow, LfRow } from "./labelingShapes";

const LABEL_COLUMNS: ColumnsType<LabelRow> = [
  {
    title: strings.analyzeLabelsColumnChunk,
    dataIndex: "chunk_id",
    key: "chunk_id",
    render: (value: string) => <span className="ui-row-id">{value}</span>,
  },
  { title: strings.analyzeLabelsColumnLabel, dataIndex: "label", key: "label" },
  {
    title: strings.analyzeLabelsColumnProbability,
    dataIndex: "probability",
    key: "probability",
    align: "right",
    render: (value: number) => <span className="ui-num">{Math.round(value * 100)}%</span>,
  },
  {
    title: strings.analyzeLabelsColumnAdded,
    key: "added",
    render: (_value: unknown, row: LabelRow) => (
      <span className="ui-num">{dateText(row.created_at)}</span>
    ),
  },
];

const LF_COLUMNS: ColumnsType<LfRow> = [
  { title: strings.analyzeLfsColumnName, dataIndex: "name", key: "name" },
  { title: strings.analyzeLfsColumnKind, dataIndex: "kind", key: "kind" },
  {
    title: strings.analyzeLfsColumnTarget,
    key: "label",
    render: (_value: unknown, row: LfRow) => row.label ?? "",
  },
  {
    title: strings.analyzeLfsColumnCoverage,
    key: "coverage",
    align: "right",
    render: (_value: unknown, row: LfRow) =>
      row.stats === null ? (
        <Typography.Text type="secondary">{strings.analyzeLfsNoStats}</Typography.Text>
      ) : (
        <span className="ui-num">{Math.round(row.stats.coverage * 100)}%</span>
      ),
  },
  {
    title: strings.analyzeLfsColumnConflicts,
    key: "conflicts",
    align: "right",
    render: (_value: unknown, row: LfRow) =>
      row.stats === null ? "" : (
        <span className="ui-num">{Math.round(row.stats.conflicts * 100)}%</span>
      ),
  },
];

/** One labels read (GET /api/labeling): the built chunk labels. */
function LabelsPane() {
  const labels = useOp("labeling-list");
  const { run, reset } = labels;
  const load = useCallback((): void => {
    reset();
    void run();
  }, [run, reset]);
  useEffect(() => {
    void run();
  }, [run]);
  const result = labels.result;
  return (
    <OpBody result={result} ok={result !== null && isLabelsEnvelope(result.data)} retry={load} opId="labeling-list">
      {result !== null &&
      isLabelsEnvelope(result.data) &&
      result.data.labels.length === 0 ? (
        <StatusBanner kind="info">{strings.analyzeLabelsEmpty}</StatusBanner>
      ) : result !== null && isLabelsEnvelope(result.data) ? (
        <div className="ui-stack">
          <Table<LabelRow>
            size="small"
            rowKey="chunk_id"
            pagination={false}
            dataSource={result.data.labels}
            columns={LABEL_COLUMNS}
          />
          <div className="ui-actions">
            <Button onClick={load}>{strings.analyzeRefresh}</Button>
          </div>
        </div>
      ) : null}
    </OpBody>
  );
}

/** One LF registry read (GET /api/labeling/lfs): names plus scorecards. */
function LfsPane() {
  const lfs = useOp("labeling-lfs");
  const { run, reset } = lfs;
  const load = useCallback((): void => {
    reset();
    void run();
  }, [run, reset]);
  useEffect(() => {
    void run();
  }, [run]);
  const result = lfs.result;
  return (
    <OpBody result={result} ok={result !== null && isLfsEnvelope(result.data)} retry={load} opId="labeling-lfs">
      {result !== null && isLfsEnvelope(result.data) && result.data.lfs.length === 0 ? (
        <StatusBanner kind="info">{strings.analyzeLfsEmpty}</StatusBanner>
      ) : result !== null && isLfsEnvelope(result.data) ? (
        <div className="ui-stack">
          <Table<LfRow>
            size="small"
            rowKey="name"
            pagination={false}
            dataSource={result.data.lfs}
            columns={LF_COLUMNS}
          />
          <div className="ui-actions">
            <Button onClick={load}>{strings.analyzeRefresh}</Button>
          </div>
        </div>
      ) : null}
    </OpBody>
  );
}

/**
 * The labeling family in one card: the built labels, the registered
 * labeling functions, the triage ranking, and the gold-set check. One
 * family, one card: the four ops share the store a labeling build
 * writes, so a tab switch beats four stacked cards.
 */
export default function LabelingCard() {
  return (
    <Card title={strings.analyzeLabelingCardTitle}>
      <Typography.Paragraph type="secondary" className="ui-flush">
        {strings.analyzeLabelingCardBody}
      </Typography.Paragraph>
      <Tabs
        items={[
          { key: "labels", label: strings.analyzeLabelingTabLabels, children: <LabelsPane /> },
          { key: "lfs", label: strings.analyzeLabelingTabLfs, children: <LfsPane /> },
          { key: "triage", label: strings.analyzeLabelingTabTriage, children: <LabelingTriagePanel /> },
          { key: "gold", label: strings.analyzeLabelingTabGold, children: <LabelingGoldPanel /> },
        ]}
      />
    </Card>
  );
}
