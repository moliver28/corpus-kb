import { Button, Card, Input, Typography } from "antd";
import { useState } from "react";
import { cancelEntry, enqueueBook, entriesOf } from "../../api/queue";
import type { QueueEnqueuePayload } from "../../api/queue";
import { runtimeConfig } from "../../api/runtimeConfig";
import type { OpResult } from "../../api/types";
import ErrorPanel from "../../components/ErrorPanel";
import StatusBanner from "../../components/StatusBanner";
import { strings } from "../../strings";
import QueueTable from "./QueueTable";
import { useQueuePoll } from "./useQueuePoll";

/**
 * The Ingest queue tab: enqueue one server-side file (idempotent per
 * content hash) and watch the worker drain the queue. Both cards speak
 * to the same three ops the CLI, MCP, socket, and HTTP surfaces use.
 */
export default function QueueCard() {
  const poll = useQueuePoll();
  const [path, setPath] = useState("");
  const [title, setTitle] = useState("");
  const [profile, setProfile] = useState("");
  const [configText, setConfigText] = useState("");
  const [hint, setHint] = useState("");
  const [enqueueing, setEnqueueing] = useState(false);
  const [enqueued, setEnqueued] = useState<QueueEnqueuePayload | null>(null);
  const [enqueueError, setEnqueueError] = useState<OpResult | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [cancelResult, setCancelResult] = useState<OpResult | null>(null);

  /** Client-side strategy-config check: empty, or one JSON object. */
  function parseConfig(): Record<string, unknown> | undefined | null {
    const text = configText.trim();
    if (text === "") {
      return undefined;
    }
    try {
      const parsed: unknown = JSON.parse(text);
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        return null;
      }
      return parsed as Record<string, unknown>;
    } catch {
      return null;
    }
  }

  async function submit(): Promise<void> {
    if (path.trim() === "") {
      setHint(strings.queuePathRequired);
      return;
    }
    const strategyConfig = parseConfig();
    if (strategyConfig === null) {
      setHint(strings.queueConfigInvalid);
      return;
    }
    setHint("");
    setEnqueueError(null);
    setEnqueueing(true);
    const cleanTitle = title.trim();
    const cleanProfile = profile.trim();
    const res = await enqueueBook({
      path: path.trim(),
      ...(cleanTitle !== "" ? { title: cleanTitle } : {}),
      ...(cleanProfile !== "" ? { profile: cleanProfile } : {}),
      ...(strategyConfig === undefined ? {} : { strategy_config: strategyConfig }),
    });
    setEnqueueing(false);
    if (res.ok && res.data !== null) {
      setEnqueued(res.data);
      poll.refresh();
    } else {
      setEnqueueError(res);
    }
  }

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
      <Card title={strings.queueFormTitle}>
        <Typography.Paragraph type="secondary">{strings.queueFormBody}</Typography.Paragraph>
        <div className="ui-actions">
          <Input
            style={{ maxWidth: 420 }}
            aria-label={strings.queuePathLabel}
            data-testid="queue-path"
            placeholder={strings.queuePathLabel}
            value={path}
            onChange={(event) => setPath(event.target.value)}
          />
          <Input
            style={{ maxWidth: 260 }}
            aria-label={strings.queueTitleLabel}
            data-testid="queue-title"
            placeholder={strings.queueTitleLabel}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>
        <div className="ui-actions">
          <Input
            style={{ maxWidth: 260 }}
            aria-label={strings.queueProfileLabel}
            data-testid="queue-profile"
            placeholder={strings.queueProfileLabel}
            value={profile}
            onChange={(event) => setProfile(event.target.value)}
          />
          <Input.TextArea
            style={{ maxWidth: 420 }}
            rows={3}
            aria-label={strings.queueConfigLabel}
            data-testid="queue-config"
            placeholder={strings.queueConfigLabel}
            value={configText}
            onChange={(event) => setConfigText(event.target.value)}
          />
        </div>
        <div className="ui-actions">
          <Button
            type="primary"
            loading={enqueueing}
            data-testid="queue-enqueue"
            onClick={() => void submit()}
          >
            {strings.queueEnqueueAction}
          </Button>
        </div>
        {hint !== "" && <StatusBanner kind="warn">{hint}</StatusBanner>}
        {enqueued !== null && (
          <div className="ui-stack">
            <StatusBanner kind="ok">{strings.queueEnqueued}</StatusBanner>
            {enqueued.deduplicated && (
              <StatusBanner kind="info">{strings.queueDeduplicated}</StatusBanner>
            )}
          </div>
        )}
        {enqueueError !== null && (
          <ErrorPanel result={enqueueError} onRetry={() => void submit()} opId="queue-enqueue" />
        )}
      </Card>
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
