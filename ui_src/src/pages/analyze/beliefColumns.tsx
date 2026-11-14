// The Beliefs card's table columns: one set for the current and
// compare rows (subject, relation, object, mean, evidence) and one for
// the era history (era window, positives, negatives, mean). Labels ride
// the strings lockstep; numbers render mono via .ui-num.
import type { ColumnsType } from "antd/es/table";
import { percentText } from "../../api/format";
import { strings } from "../../strings/analyze";
import type { BeliefRow, EraRow } from "./beliefsShapes";

export const CURRENT_COLUMNS: ColumnsType<BeliefRow> = [
  { title: strings.analyzeBeliefsColumnSubject, dataIndex: "subject", key: "subject" },
  { title: strings.analyzeBeliefsColumnRelation, dataIndex: "relation", key: "relation" },
  { title: strings.analyzeBeliefsColumnObject, dataIndex: "object", key: "object" },
  {
    title: strings.analyzeBeliefsColumnMean,
    key: "mean",
    align: "right",
    render: (_v, row) => (
      <span className="ui-num">{percentText(row.alpha / (row.alpha + row.beta))}</span>
    ),
  },
  {
    title: strings.analyzeBeliefsColumnEvidence,
    dataIndex: "evidence_count",
    key: "evidence_count",
    align: "right",
    render: (value: number) => <span className="ui-num">{value}</span>,
  },
];

export const ERA_COLUMNS: ColumnsType<EraRow> = [
  {
    title: strings.analyzeBeliefsColumnEra,
    key: "era",
    render: (_v, row) => (
      <span className="ui-num">
        {row.start.slice(0, 10)} - {row.end.slice(0, 10)}
      </span>
    ),
  },
  {
    title: strings.analyzeBeliefsColumnPositives,
    dataIndex: "positives",
    key: "positives",
    align: "right",
    render: (value: number) => <span className="ui-num">{value}</span>,
  },
  {
    title: strings.analyzeBeliefsColumnNegatives,
    dataIndex: "negatives",
    key: "negatives",
    align: "right",
    render: (value: number) => <span className="ui-num">{value}</span>,
  },
  {
    title: strings.analyzeBeliefsColumnMean,
    key: "mean",
    align: "right",
    render: (_v, row) => <span className="ui-num">{percentText(row.mean)}</span>,
  },
];
