import { Button, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { percentText } from "../../api/format";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings/analyze";
import { envelopeNote, isTriageEnvelope } from "./labelingShapes";
import type { TriageRow } from "./labelingShapes";

const QUEUE_COLUMNS: ColumnsType<TriageRow> = [
  {
    title: strings.analyzeTriageColumnChunk,
    dataIndex: "chunk_id",
    key: "chunk_id",
    render: (value: string) => <span className="ui-row-id">{value}</span>,
  },
  { title: strings.analyzeTriageColumnAssigned, dataIndex: "assigned_label", key: "assigned_label" },
  {
    title: strings.analyzeTriageColumnSupport,
    dataIndex: "oos_probability",
    key: "oos_probability",
    align: "right",
    render: (value: number) => <span className="ui-num">{percentText(value)}</span>,
  },
  {
    title: strings.analyzeTriageColumnRunnerUp,
    dataIndex: "runner_up_label",
    key: "runner_up_label",
  },
];

/**
 * The triage run (POST /api/labeling/triage, read-only): ranks the
 * built labels whose assigned label holds weak out-of-sample support.
 * The queue is advice only — the op never relabels, so there is no
 * accept or reject here; a reviewer reads the chunk in Review instead.
 */
export default function LabelingTriagePanel() {
  const triage = useOp("labeling-triage");
  async function rank(): Promise<void> {
    await triage.run({});
  }
  const result = triage.result;
  const data = result !== null && isTriageEnvelope(result.data) ? result.data : null;
  const note = envelopeNote(result?.data);
  const shown = data?.queue.length ?? 0;
  return (
    <div className="ui-stack-gap">
      <p className="ui-flush">{strings.analyzeTriageBody}</p>
      <div className="ui-actions">
        <Button type="primary" loading={triage.running} onClick={() => void rank()}>
          {strings.analyzeTriageAction}
        </Button>
      </div>
      <OpBody result={result} ok={data !== null} retry={() => void rank()} opId="labeling-triage">
        {data !== null && data.status !== "success" ? (
          <div className="ui-stack">
            <StatusBanner kind="warn">{note.reason}</StatusBanner>
            {note.remedy !== "" && (
              <StatusBanner kind="info">
                <strong>{strings.remediationLabel}: </strong>
                {note.remedy}
              </StatusBanner>
            )}
          </div>
        ) : data !== null && shown === 0 ? (
          <StatusBanner kind="info">{strings.analyzeTriageEmpty}</StatusBanner>
        ) : data !== null ? (
          <div className="ui-stack">
            {/* The server's own success reason (the gold panel's one
                treatment): it names the flagged total and the advisory
                contract, so the panel computes no line of its own. */}
            {note.reason !== "" && <StatusBanner kind="info">{note.reason}</StatusBanner>}
            <Table<TriageRow>
              size="small"
              rowKey="chunk_id"
              pagination={false}
              dataSource={data.queue}
              columns={QUEUE_COLUMNS}
            />
          </div>
        ) : null}
      </OpBody>
    </div>
  );
}
