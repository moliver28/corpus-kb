import { Button, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRef, useState } from "react";
import { percentText } from "../../api/format";
import LabeledField, { useFirstErrorFocus } from "../../components/LabeledField";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings/analyze";
import { envelopeNote, isGoldEnvelope, rangeText } from "./labelingShapes";
import type { GoldRow } from "./labelingShapes";

const GOLD_COLUMNS: ColumnsType<GoldRow> = [
  { title: strings.analyzeGoldColumnClass, dataIndex: "class_name", key: "class_name" },
  { title: strings.analyzeGoldColumnState, dataIndex: "status", key: "status" },
  {
    title: strings.analyzeGoldColumnAgreement,
    dataIndex: "raw_accuracy",
    key: "raw_accuracy",
    align: "right",
    render: (value: number) => <span className="ui-num">{percentText(value)}</span>,
  },
  {
    title: strings.analyzeGoldColumnEstimate,
    dataIndex: "estimate",
    key: "estimate",
    align: "right",
    render: (value: number) => <span className="ui-num">{percentText(value)}</span>,
  },
  {
    title: strings.analyzeGoldColumnRange,
    key: "range",
    align: "right",
    render: (_value: unknown, row: GoldRow) => (
      <span className="ui-num">{rangeText(row.lower, row.upper)}</span>
    ),
  },
  {
    title: strings.analyzeGoldColumnGold,
    dataIndex: "n_gold",
    key: "n_gold",
    align: "right",
    render: (value: number) => <span className="ui-num">{value}</span>,
  },
];

/**
 * The gold-set check (POST /api/labeling/gold-check): the built labels
 * score against a gold dev set, one class at a time. Both numbers per
 * class show — the raw agreement and the corrected estimate — so the
 * corrected number never travels alone.
 */
export default function LabelingGoldPanel() {
  const gold = useOp("labeling-gold-check");
  const [goldFile, setGoldFile] = useState("");
  const [error, setError] = useState("");
  const { rootRef, flag } = useFirstErrorFocus();
  const bodyRef = useRef<Record<string, unknown>>({});
  async function check(): Promise<void> {
    if (goldFile.trim() === "") {
      setError(strings.analyzeGoldFileRequired);
      flag();
      return;
    }
    setError("");
    bodyRef.current = { gold_file: goldFile.trim() };
    await gold.run(bodyRef.current);
  }
  const result = gold.result;
  const data = result !== null && isGoldEnvelope(result.data) ? result.data : null;
  const note = envelopeNote(result?.data);
  return (
    <div className="ui-stack-gap">
      <p className="ui-flush">{strings.analyzeGoldBody}</p>
      <div className="ui-actions" ref={rootRef}>
        <LabeledField
          label={strings.analyzeGoldFileLabel}
          value={goldFile}
          onChange={setGoldFile}
          error={error}
          required
          machine
          name="gold_file"
          widthClass="ui-narrow--form"
          onPressEnter={() => void check()}
        />
        <Button type="primary" loading={gold.running} onClick={() => void check()}>
          {strings.analyzeGoldAction}
        </Button>
      </div>
      <OpBody result={result} ok={data !== null} retry={() => void check()} opId="labeling-gold-check">
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
        ) : data !== null && data.validations.length === 0 ? (
          <StatusBanner kind="info">{strings.analyzeGoldEmpty}</StatusBanner>
        ) : data !== null ? (
          <div className="ui-stack">
            {note.reason !== "" && <StatusBanner kind="info">{note.reason}</StatusBanner>}
            <Table<GoldRow>
              size="small"
              rowKey="class_name"
              pagination={false}
              dataSource={data.validations}
              columns={GOLD_COLUMNS}
            />
          </div>
        ) : null}
      </OpBody>
    </div>
  );
}
