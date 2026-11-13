import { Button, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { dateText } from "../../api/format";
import ConfirmGate from "../../components/ConfirmGate";
import { windowPagerTotal } from "../../hooks/useServerWindow";
import { strings } from "../../strings/review";
import type { DocumentRow } from "./shapes";

/**
 * The corpus document rows: one window of the server's pages (the read
 * op answers limit/offset windows, never the whole corpus). Delete is a
 * per-row confirm gate; Details opens the tag/metadata drawer; Analyze
 * opens the drawer straight on its NLP panel (Phase-2 Wave 5).
 */
export default function DocumentsTable({
  rows,
  page,
  pageSize,
  hasMore,
  onPage,
  onDetails,
  onAnalyze,
  onDelete,
}: {
  rows: DocumentRow[];
  page: number;
  pageSize: number;
  hasMore: boolean;
  onPage: (page: number) => void;
  onDetails: (row: DocumentRow) => void;
  onAnalyze: (row: DocumentRow) => void;
  onDelete: (row: DocumentRow) => void;
}) {
  const columns: ColumnsType<DocumentRow> = [
    { title: strings.reviewDocsColumnSource, dataIndex: "source", key: "source" },
    { title: strings.reviewDocsColumnType, dataIndex: "source_type", key: "source_type" },
    {
      title: strings.reviewDocsColumnChunks,
      dataIndex: "chunk_count",
      key: "chunk_count",
      align: "right",
      render: (value: number) => <span className="ui-num">{value}</span>,
    },
    {
      title: strings.reviewDocsColumnAdded,
      key: "added",
      render: (_value: unknown, row: DocumentRow) => (
        <span className="ui-num">{dateText(row.created_at)}</span>
      ),
    },
    {
      title: strings.reviewDocsColumnActions,
      key: "actions",
      render: (_value: unknown, row: DocumentRow) => (
        <div className="ui-actions">
          <Button size="small" onClick={() => onDetails(row)}>
            {strings.reviewDocsDetails}
          </Button>
          <Button size="small" onClick={() => onAnalyze(row)}>
            {strings.reviewDocsAnalyze}
          </Button>
          <ConfirmGate
            title={strings.reviewDocsDeleteTitle}
            description={strings.reviewDocsDeleteBody.replace("{source}", row.source)}
            trigger={
              <Button danger size="small">
                {strings.reviewDocsDelete}
              </Button>
            }
            onConfirm={() => onDelete(row)}
          />
        </div>
      ),
    },
  ];
  // The read op has no total count, so the window writes the pager
  // size (the seam's windowPagerTotal): one row over the page size
  // means a next page exists. No size changer: the server window is
  // the page size.
  const total = windowPagerTotal(page, pageSize, rows.length, hasMore);
  return (
    <Table<DocumentRow>
      size="small"
      rowKey="doc_id"
      dataSource={rows}
      columns={columns}
      pagination={{
        current: page,
        pageSize,
        total,
        showSizeChanger: false,
        onChange: onPage,
      }}
    />
  );
}
