// The chat honest states (Phase-2 Wave 7, step 4's rows on screen).
// Disabled -> the honest off panel. Enabled but a fail/warn check (no
// login in the pin, precedence trap) -> the typed blocked list with the
// doctor's fix text. Never a spinner that pretends a session can start.
import { Alert, Typography } from "antd";
import ErrorPanel from "../../components/ErrorPanel";
import type { OpResult } from "../../api/types";
import type { ChatStatus } from "../../hooks/useChatSession";
import { strings } from "../../strings/chat";

export default function ChatStatusPanel({
  status,
  error,
  onRetry,
}: {
  status: ChatStatus | null;
  error: OpResult | null;
  onRetry: () => void;
}) {
  if (error !== null) {
    return <ErrorPanel result={error} onRetry={onRetry} opId="chat-status" />;
  }
  if (status === null) {
    // Loading asserts nothing: no session claim before the status lands.
    return <Typography.Text type="secondary">{strings.chatStatusLoading}</Typography.Text>;
  }
  if (!status.enabled) {
    return (
      <Alert
        type="info"
        showIcon
        message={strings.chatStatusTitle}
        description={strings.chatDisabledBody}
      />
    );
  }
  const blocking = status.checks.filter((check) => check.status === "fail" || check.status === "warn");
  if (blocking.length > 0) {
    return (
      <div>
        <Alert
          type="warning"
          showIcon
          message={strings.chatStatusTitle}
          description={strings.chatBlockedBody}
        />
        <ul className="ui-gate-list" aria-label={strings.chatStatusChecks}>
          {blocking.map((check) => (
            <li key={check.name}>
              <Typography.Text strong>{check.name}</Typography.Text>
              <br />
              <Typography.Text type="secondary">{check.detail}</Typography.Text>
              {check.fix !== "" && (
                <>
                  <br />
                  <Typography.Text>Fix: {check.fix}</Typography.Text>
                </>
              )}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  return null;
}
