import { Button, Card, Typography } from "antd";
import { useState } from "react";
import { enqueueBook } from "../../api/queue";
import type { QueueEnqueuePayload } from "../../api/queue";
import type { OpResult } from "../../api/types";
import ErrorPanel from "../../components/ErrorPanel";
import LabeledField, { useFirstErrorFocus } from "../../components/LabeledField";
import StatusBanner from "../../components/StatusBanner";
import { strings } from "../../strings/ingest";

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
  const [pathError, setPathError] = useState("");
  const [configError, setConfigError] = useState("");
  const [enqueueing, setEnqueueing] = useState(false);
  const [enqueued, setEnqueued] = useState<QueueEnqueuePayload | null>(null);
  const [enqueueError, setEnqueueError] = useState<OpResult | null>(null);
  const { rootRef: errorFocusRef, flag } = useFirstErrorFocus();

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
      setPathError(strings.queuePathRequired);
    } else {
      setPathError("");
    }
    const strategyConfig = parseConfig();
    if (strategyConfig === null) {
      setConfigError(strings.queueConfigInvalid);
    } else {
      setConfigError("");
    }
    if (path.trim() === "" || strategyConfig === null) {
      flag();
      return;
    }
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
      <div ref={errorFocusRef}>
        <div className="ui-actions">
          <LabeledField
            label={strings.queuePathLabel}
            value={path}
            onChange={setPath}
            error={pathError}
            required
            machine
            name="path"
            widthClass="ui-narrow--form"
            id="queue-path"
          />
          <LabeledField
            label={strings.queueTitleLabel}
            value={title}
            onChange={setTitle}
            name="title"
            widthClass="ui-narrow--small"
            id="queue-title"
          />
        </div>
        <div className="ui-actions">
          <LabeledField
            label={strings.queueProfileLabel}
            value={profile}
            onChange={setProfile}
            machine
            name="profile"
            widthClass="ui-narrow--small"
            id="queue-profile"
          />
          <LabeledField
            label={strings.queueConfigLabel}
            value={configText}
            onChange={setConfigText}
            error={configError}
            machine
            multiline
            name="strategy_config"
            widthClass="ui-narrow--form"
            id="queue-config"
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
      </div>
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
