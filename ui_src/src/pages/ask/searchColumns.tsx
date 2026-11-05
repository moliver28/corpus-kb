import { Button } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { strings } from "../../strings";
import { scoreText, type SearchRow } from "./shapes";

/**
 * One source cell: the label with its chunk id underneath. The id is
 * the value the verify tab's chunk field asks for, so a copy button
 * rides with it and shows a short note after the write lands.
 */
function ChunkIdCell({
  source,
  chunkId,
  copied,
  onCopy,
}: {
  source: string;
  chunkId: string;
  copied: boolean;
  onCopy: (chunkId: string) => void;
}): ReactNode {
  return (
    <div>
      {source}
      <div className="ui-row-id">
        <span>{chunkId}</span>
        <Button type="link" size="small" className="ui-row-copy" onClick={() => onCopy(chunkId)}>
          {copied ? strings.copiedNote : strings.askCopyChunkId}
        </Button>
      </div>
    </div>
  );
}

/** The expanded row: the full result text the cell only previews. */
function expandRow(row: SearchRow): ReactNode {
  return <span>{row.text}</span>;
}

/**
 * The three search modes share one result table: a capped page size,
 * an ellipsis preview of the text (the full text stays one expand
 * away), and the copyable chunk id per row.
 */
export function columnsOf(
  copiedId: string | null,
  onCopy: (chunkId: string) => void,
): ColumnsType<SearchRow> {
  return [
    {
      title: strings.askColumnSource,
      dataIndex: "source",
      key: "source",
      render: (_value: unknown, row: SearchRow) => (
        <ChunkIdCell
          source={row.source}
          chunkId={row.chunk_id}
          copied={copiedId === row.chunk_id}
          onCopy={onCopy}
        />
      ),
    },
    { title: strings.askColumnText, dataIndex: "text", key: "text", ellipsis: true },
    {
      title: strings.askColumnScore,
      key: "score",
      render: (_value: unknown, row: SearchRow) => (
        <span className="ui-num">{scoreText(row.score)}</span>
      ),
    },
  ];
}

export const SEARCH_PAGE_SIZE = 10;

export const searchExpandable = { expandedRowRender: expandRow };
