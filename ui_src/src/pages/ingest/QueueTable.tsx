import { Button, Popconfirm, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { strings } from "../../strings";
import type { QueueEntry } from "../../api/queue";

/** The statuses a cancel may still stop (queue_tools.py queue_cancel). */
const ACTIVE_STATUSES: ReadonlySet<string> = new Set(["pending", "running"]);

/** The tag color names the state; the status word stays the signal. */
const TAG_COLOR: Record<string, string> = {
  pending: "default",
  running: "processing",
  done: "success",
  failed: "error",
  cancelled: "warning",
};

function dash(value: string | null): string {
  return value !== null && value !== "" ? value : "-";
}

/** The pages and chunks line a worker fills in; nulls stay a dash. */
function progressOf(entry: QueueEntry): string {
  const parts: string[] = [];
  if (typeof entry.pages_done === "number" && typeof entry.pages_total === "number") {
    parts.push(
      strings.queuePagesLine
        .replace("{done}", String(entry.pages_done))
        .replace("{total}", String(entry.pages_total)),
    );
  }
  if (typeof entry.chunks === "number") {
    parts.push(strings.queueChunksLine.replace("{chunks}", String(entry.chunks)));
  }
  return parts.length > 0 ? parts.join("; ") : "-";
}

function attemptsOf(entry: QueueEntry): string {
  return strings.queueAttemptsLine
    .replace("{done}", String(entry.attempts))
    .replace("{max}", String(entry.max_attempts));
}

function elapsedOf(entry: QueueEntry): string {
  if (typeof entry.elapsed_seconds !== "number") {
    return "-";
  }
  return strings.queueElapsedLine.replace("{sec}", String(Math.round(entry.elapsed_seconds)));
}

/**
 * The queue table: one row per pending or running entry, status words as
 * tags, worker progress beside them, and a confirm-gated cancel per
 * active row. Finished rows never appear here (queue_status serves the
 * active list; a single-row probe shows them on its own).
 */
export default function QueueTable({
  entries,
  cancellingId,
  onCancel,
}: {
  entries: QueueEntry[];
  cancellingId: string | null;
  onCancel: (queueId: string) => void;
}) {
  const columns: ColumnsType<QueueEntry> = [
    {
      title: strings.queueColumnFile,
      key: "file",
      render: (_: unknown, record: QueueEntry) =>
        record.title !== null && record.title !== "" ? record.title : record.path,
    },
    {
      title: strings.queueColumnStatus,
      key: "status",
      render: (_: unknown, record: QueueEntry) => (
        <Tag color={TAG_COLOR[record.status] ?? "default"}>{record.status}</Tag>
      ),
    },
    {
      title: strings.queueColumnProgress,
      key: "progress",
      render: (_: unknown, record: QueueEntry) => progressOf(record),
    },
    {
      title: strings.queueColumnAttempts,
      key: "attempts",
      render: (_: unknown, record: QueueEntry) => attemptsOf(record),
    },
    {
      title: strings.queueColumnElapsed,
      key: "elapsed",
      render: (_: unknown, record: QueueEntry) => elapsedOf(record),
    },
    {
      title: strings.queueColumnError,
      key: "last_error",
      ellipsis: true,
      render: (_: unknown, record: QueueEntry) => dash(record.last_error),
    },
    {
      title: strings.queueColumnActions,
      key: "actions",
      render: (_: unknown, record: QueueEntry) =>
        ACTIVE_STATUSES.has(record.status) ? (
          <Popconfirm
            title={strings.queueCancelConfirmTitle}
            description={strings.queueCancelConfirmBody}
            okText={strings.queueCancelConfirmOk}
            cancelText={strings.queueCancelConfirmDismiss}
            onConfirm={() => onCancel(record.id)}
          >
            <Button
              size="small"
              loading={cancellingId === record.id}
              disabled={cancellingId !== null}
              data-testid={`queue-cancel-${record.id}`}
            >
              {strings.queueCancel}
            </Button>
          </Popconfirm>
        ) : null,
    },
  ];
  return (
    <Table<QueueEntry>
      size="small"
      rowKey="id"
      pagination={false}
      dataSource={entries}
      columns={columns}
    />
  );
}
