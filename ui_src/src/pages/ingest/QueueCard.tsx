import { Card } from "antd";
import { useState } from "react";
import { cancelEntry, entriesOf } from "../../api/queue";
import type { QueueStatusPayload } from "../../api/queue";
import { runtimeConfig } from "../../api/runtimeConfig";
import type { OpResult } from "../../api/types";
import ErrorPanel from "../../components/ErrorPanel";
import StatusBanner from "../../components/StatusBanner";
import { usePoll } from "../../hooks/usePoll";
import { strings } from "../../strings";
import EnqueueCard from "./EnqueueCard";
import QueueTable from "./QueueTable";

/**
 * The Ingest queue tab: one enqueue form and the polled table where the
 * worker drains the queue. Both cards speak to the same three ops the
 * CLI, MCP, socket, and HTTP surfaces use.
 */
export default function QueueCard() {
  const poll = usePoll<QueueStatusPayload>("queue-status", {});
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [cancelResult, setCancelResult] = useState<OpResult | null>(null);

  async function cancel(queueId: string): Promise<void> {
    setCancellingId(queueId);
    setCancelResult(null);
    const res = await cancelEntry(queueId);
    setCancellingId(null);
    setCancelResult(res);
    if (res.ok) {
      poll.refresh();
    }
  }

  const entries = entriesOf(poll.result === null ? null : poll.result.data);
  const pollSec = Math.round(runtimeConfig().pollMs / 1000);
  return (
    <div className="ui-stack">
      <EnqueueCard onEnqueued={poll.refresh} />
      <Card title={strings.queueTableTitle}>
        {poll.paused ? (
          <StatusBanner kind="info">{strings.queuePaused}</StatusBanner>
        ) : poll.backingOff ? (
          <StatusBanner kind="warn">{strings.queueBackoff}</StatusBanner>
        ) : (
          <StatusBanner kind="info">
            {strings.queuePolling.replace("{sec}", String(pollSec))}
          </StatusBanner>
        )}
        {cancelResult !== null &&
          (cancelResult.ok ? (
            <StatusBanner kind="ok">{strings.queueCancelDone}</StatusBanner>
          ) : (
            <ErrorPanel result={cancelResult} opId="queue-cancel" />
          ))}
        {poll.result !== null &&
          (entries.length === 0 ? (
            <StatusBanner kind="info">{strings.queueEmpty}</StatusBanner>
          ) : (
            <QueueTable
              entries={entries}
              cancellingId={cancellingId}
              onCancel={(id) => void cancel(id)}
            />
          ))}
      </Card>
    </div>
  );
}
