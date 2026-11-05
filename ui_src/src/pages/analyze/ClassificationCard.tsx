import { Button, Card, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useCallback, useEffect } from "react";
import { percentText } from "../../api/format";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { isPredictionsEnvelope } from "./shapes";
import type { PredictionRow } from "./shapes";

const COLUMNS: ColumnsType<PredictionRow> = [
  {
    title: strings.analyzeClassificationColumnChunk,
    dataIndex: "chunk_id",
    key: "chunk_id",
    render: (value: string) => <span className="ui-row-id">{value}</span>,
  },
  {
    title: strings.analyzeClassificationColumnSet,
    dataIndex: "set_id",
    key: "set_id",
    render: (value: string) => <span className="ui-row-id">{value}</span>,
  },
  { title: strings.analyzeClassificationColumnLabel, dataIndex: "label", key: "label" },
  {
    title: strings.analyzeClassificationColumnProbability,
    dataIndex: "probability",
    key: "probability",
    align: "right",
    render: (value: number) => <span className="ui-num">{percentText(value)}</span>,
  },
  {
    title: strings.analyzeClassificationColumnCalibrated,
    dataIndex: "calibrated_probability",
    key: "calibrated_probability",
    align: "right",
    render: (value: number | null) => (
      <span className="ui-num">{value === null ? "-" : percentText(value)}</span>
    ),
  },
  { title: strings.analyzeClassificationColumnTier, dataIndex: "tier", key: "tier" },
  {
    title: strings.analyzeClassificationColumnReview,
    dataIndex: "review",
    key: "review",
    render: (value: boolean) => (value ? <Tag>{strings.analyzeClassificationReviewTag}</Tag> : ""),
  },
];

/**
 * The built predictions (GET /api/classification): one read on mount,
 * the shared table pattern, and an honest empty state that names the
 * build action that fills it.
 */
export default function ClassificationCard() {
  const predictions = useOp("classification-list");
  const { run, reset } = predictions;
  const load = useCallback((): void => {
    reset();
    void run();
  }, [run, reset]);
  useEffect(() => {
    void run();
  }, [run]);
  const result = predictions.result;
  const data = result !== null && isPredictionsEnvelope(result.data) ? result.data : null;
  let body: ReactNode = null;
  if (data !== null && data.predictions.length === 0) {
    body = <StatusBanner kind="info">{strings.analyzeClassificationEmpty}</StatusBanner>;
  } else if (data !== null) {
    body = (
      <div className="ui-stack">
        <Table<PredictionRow>
          size="small"
          rowKey={(row) => `${row.set_id}-${row.chunk_id}`}
          pagination={false}
          dataSource={data.predictions}
          columns={COLUMNS}
        />
        <div className="ui-actions">
          <Button onClick={load}>{strings.analyzeRefresh}</Button>
        </div>
      </div>
    );
  }
  return (
    <Card title={strings.analyzeClassificationReadTitle}>
      <OpBody
        result={result}
        ok={data !== null}
        retry={load}
        opId="classification-list"
      >
        {body}
      </OpBody>
    </Card>
  );
}
