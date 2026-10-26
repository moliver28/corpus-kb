import { Table, type TableProps } from "antd";
import { strings } from "../strings";

export interface DocumentRow {
  id: string;
  title: string;
  created?: string;
  status?: string;
}

const COLUMNS: TableProps<DocumentRow>["columns"] = [
  { title: strings.docsColumnTitle, dataIndex: "title", key: "title" },
  { title: strings.docsColumnAdded, dataIndex: "created", key: "created" },
  { title: strings.docsColumnStatus, dataIndex: "status", key: "status" },
];

/** The corpus document list (one row per ingested file). */
export default function DocumentsTable({
  rows,
  loading = false,
}: {
  rows: DocumentRow[];
  loading?: boolean;
}) {
  return (
    <Table<DocumentRow>
      rowKey="id"
      size="small"
      loading={loading}
      dataSource={rows}
      columns={COLUMNS}
      locale={{ emptyText: strings.docsEmpty }}
    />
  );
}
