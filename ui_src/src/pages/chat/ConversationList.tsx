// The chat session list (Phase-2 Wave 7): the CLI's own session store
// ([D9]) read through GET /api/chat/sessions. Activating one resumes it
// server-side; there is NO delete affordance — no SDK helper offers it,
// so the honest absence shows ([D9]).
import { Conversations } from "@ant-design/x";
import { Button, Typography } from "antd";
import { useEffect, useState } from "react";
import { callOp } from "../../api/client";
import { strings } from "../../strings/chat";

export interface SessionRow {
  session_id: string;
  summary: string;
  last_modified: number;
  first_prompt: string | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export default function ConversationList({
  activeId,
  onResume,
}: {
  activeId: string | null;
  onResume: (sessionId: string) => void;
}) {
  const [rows, setRows] = useState<SessionRow[] | null>(null);

  useEffect(() => {
    let live = true;
    void callOp<{ result?: { sessions?: unknown[] } }>("/api/chat/sessions?limit=20", {
      method: "GET",
    }).then((result) => {
      if (!live || !result.ok || !isRecord(result.data) || !isRecord(result.data.result)) return;
      const sessions = Array.isArray(result.data.result.sessions) ? result.data.result.sessions : [];
      setRows(
        sessions.filter(isRecord).map((row) => ({
          session_id: String(row.session_id ?? ""),
          summary: String(row.summary ?? ""),
          last_modified: typeof row.last_modified === "number" ? row.last_modified : 0,
          first_prompt: typeof row.first_prompt === "string" ? row.first_prompt : null,
        })),
      );
    });
    return () => {
      live = false;
    };
  }, []);

  const refresh = (): void => {
    setRows(null);
    void callOp<{ result?: { sessions?: unknown[] } }>("/api/chat/sessions?limit=20", { method: "GET" }).then(
      (result) => {
        if (result.ok && isRecord(result.data) && isRecord(result.data.result) && Array.isArray(result.data.result.sessions)) {
          setRows(
            result.data.result.sessions.filter(isRecord).map((row) => ({
              session_id: String(row.session_id ?? ""),
              summary: String(row.summary ?? ""),
              last_modified: typeof row.last_modified === "number" ? row.last_modified : 0,
              first_prompt: typeof row.first_prompt === "string" ? row.first_prompt : null,
            })),
          );
        }
      },
    );
  };

  if (rows === null) {
    return <Typography.Text type="secondary">{strings.chatSessionActive}</Typography.Text>;
  }
  if (rows.length === 0) {
    return (
      <div className="ui-chat-history">
        <Typography.Text type="secondary">{strings.chatHistoryEmpty}</Typography.Text>
        <Button size="small" onClick={refresh}>
          {strings.chatSessionNew}
        </Button>
      </div>
    );
  }
  return (
    <div className="ui-chat-history">
      <Typography.Text strong className="ui-chat-history__title">
        {strings.chatHistoryTitle}
      </Typography.Text>
      <Conversations
        items={rows.map((row) => ({
          key: row.session_id,
          label: row.summary !== "" ? row.summary : row.session_id,
        }))}
        activeKey={activeId ?? undefined}
        onActiveChange={(key: string | number) => onResume(String(key))}
      />
      <Button size="small" onClick={refresh}>
        {strings.chatSessionNew}
      </Button>
    </div>
  );
}
