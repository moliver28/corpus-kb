import { Button, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { strings } from "../../strings";
import { confidenceText, reviewStateWord } from "./shapes";
import type { AssignmentRow } from "./shapes";

/** The coding queue rows: one accept and one override action per row. */
export default function AssignmentsTable({
  rows,
  onAccept,
  onOverride,
}: {
  rows: AssignmentRow[];
  onAccept: (row: AssignmentRow) => void;
  onOverride: (row: AssignmentRow) => void;
}) {
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
          <Button size="small" onClick={() => onAccept(row)}>
            {strings.reviewAccept}
          </Button>
          <Button size="small" onClick={() => onOverride(row)}>
            {strings.reviewOverride}
          </Button>
        </div>
      ),
    },
  ];
  return (
    <Table<AssignmentRow>
      size="small"
      rowKey="assignment_id"
      pagination={false}
      dataSource={rows}
      columns={columns}
    />
  );
}
