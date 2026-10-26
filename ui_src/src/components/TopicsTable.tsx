import { Table, Tag, type TableProps } from "antd";
import { strings } from "../strings";

export interface TopicRow {
  id: string;
  label: string;
  size: number;
  keywords: string[];
}

const COLUMNS: TableProps<TopicRow>["columns"] = [
  { title: strings.topicsColumnLabel, dataIndex: "label", key: "label" },
  { title: strings.topicsColumnSize, dataIndex: "size", key: "size" },
  {
    title: strings.topicsColumnKeywords,
    key: "keywords",
    render: (_value: unknown, row: TopicRow) => (
      <>
        {row.keywords.map((keyword) => (
          <Tag key={keyword}>{keyword}</Tag>
        ))}
      </>
    ),
  },
];

/** The built topic list: label, chunk count, keywords. */
export default function TopicsTable({
  rows,
  loading = false,
}: {
  rows: TopicRow[];
  loading?: boolean;
}) {
  return (
    <Table<TopicRow>
      rowKey="id"
      size="small"
      loading={loading}
      dataSource={rows}
      columns={COLUMNS}
      locale={{ emptyText: strings.topicsEmpty }}
    />
  );
}
