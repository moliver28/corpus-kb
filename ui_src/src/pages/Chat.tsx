// The Chat page (Phase-2 Wave 7): status first (the honest states),
// then the transcript, the composer, and the session list. The page is
// a lazy domain view; @ant-design/x rides this chunk only (the
// ui-import-graph gate). X defaults we render are overridden from the
// chat strings table ([D12]).
import { XProvider } from "@ant-design/x";
import { Card, Typography } from "antd";
import antdEnUS from "antd/locale/en_US";
import { useEffect } from "react";
import ErrorPanel from "../components/ErrorPanel";
import { useChatSession } from "../hooks/useChatSession";
import { strings } from "../strings/chat";
import ChatComposer from "./chat/ChatComposer";
import ChatPanel from "./chat/ChatPanel";
import ChatStatusPanel from "./chat/ChatStatusPanel";
import ConversationList from "./chat/ConversationList";

export default function Chat() {
  const chat = useChatSession();
  const { sessionId, backend, turns, flight, error, status, busy, start, send, stop, setError } =
    chat;

  // First entry opens a session; the panel stays honest while it runs.
  useEffect(() => {
    if (status !== null && status.enabled && sessionId === null) {
      void start(null);
    }
  }, [status, sessionId, start]);

  const blocked = status !== null && !status.enabled;
  const retry = (): void => {
    setError(null);
    if (status !== null && status.enabled && sessionId === null) {
      void start(null);
    }
  };

  return (
    <Card title={strings.chatTitle} className="ui-chat-card">
      <Typography.Paragraph type="secondary">{strings.chatBody}</Typography.Paragraph>
      {error !== null && turns.length === 0 && (
        <ErrorPanel result={error} onRetry={retry} opId="chat" />
      )}
      <ChatStatusPanel status={status} error={error !== null && turns.length === 0 ? null : error} onRetry={retry} />
      {!blocked && (
        <XProvider
          locale={{
            ...antdEnUS,
            locale: "en",
            Sender: {
              stopLoading: strings.chatStopLabel,
              speechRecording: strings.chatSpeechRecording,
            },
          }}
        >
          <div className="ui-chat-layout">
            <div className="ui-chat-main">
              <ChatPanel turns={turns} flight={flight} />
              <ChatComposer
                disabled={status !== null && !status.enabled}
                busy={busy}
                flight={flight}
                onSend={(text) => void send(text)}
                onStop={stop}
              />
              {backend !== "" && (
                <Typography.Text type="secondary" className="ui-chat-backend">
                  {strings.chatStatusEnabled.replace("{backend}", backend)}
                </Typography.Text>
              )}
            </div>
            <ConversationList
              activeId={sessionId}
              onResume={(resumeId) => void start(resumeId)}
            />
          </div>
        </XProvider>
      )}
    </Card>
  );
}
