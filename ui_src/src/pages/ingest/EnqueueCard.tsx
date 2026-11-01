import { Button, Card, Input, Typography } from "antd";
import { useState } from "react";
import { enqueueBook } from "../../api/queue";
import type { QueueEnqueuePayload } from "../../api/queue";
import type { OpResult } from "../../api/types";
import ErrorPanel from "../../components/ErrorPanel";
import StatusBanner from "../../components/StatusBanner";
import { strings } from "../../strings";

/**
 * The enqueue form (one server-side file, idempotent per content hash):
 * path, optional title and profile, and a client-side strategy-config
 * JSON check. onEnqueued fires after a settled enqueue so the polled
 * table next to it refreshes at once.
 */
export default function EnqueueCard({ onEnqueued }: { onEnqueued: () => void }) {
  const [path, setPath] = useState("");
  const [title, setTitle] = useState("");
  const [profile, setProfile] = useState("");
  const [configText, setConfigText] = useState("");
  const [hint, setHint] = useState("");
  const [enqueueing, setEnqueueing] = useState(false);
  const [enqueued, setEnqueued] = useState<QueueEnqueuePayload | null>(null);
  const [enqueueError, setEnqueueError] = useState<OpResult | null>(null);

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
      onEnqueued();
    } else {
      setEnqueueError(res);
    }
  }

  return (
    <Card title={strings.queueFormTitle}>
      <Typography.Paragraph type="secondary">{strings.queueFormBody}</Typography.Paragraph>
      <div className="ui-actions">
        <Input
          className="ui-narrow--form"
          aria-label={strings.queuePathLabel}
          data-testid="queue-path"
          placeholder={strings.queuePathLabel}
          value={path}
          onChange={(event) => setPath(event.target.value)}
        />
        <Input
          className="ui-narrow--small"
          aria-label={strings.queueTitleLabel}
          data-testid="queue-title"
          placeholder={strings.queueTitleLabel}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className="ui-actions">
        <Input
          className="ui-narrow--small"
          aria-label={strings.queueProfileLabel}
          data-testid="queue-profile"
          placeholder={strings.queueProfileLabel}
          value={profile}
          onChange={(event) => setProfile(event.target.value)}
        />
        <Input.TextArea
          className="ui-narrow--form"
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
  );
}
