// The one chat session hook (Phase-2 Wave 7): single-flight per turn.
// Streaming is NOT polling: one SSE stream per sent message, no poll
// loop, no pollCache involvement. The session rides the server (TTL +
// transcripts); the page reconnects by resume on reopen and never
// pretends an old stream lives.
import { useCallback, useEffect, useRef, useState } from "react";
import { callOp } from "../api/client";
import { callOpStream } from "../api/stream";
import type { OpResult } from "../api/types";

/** One doctor row of GET /api/chat/status (the honest-state payload). */
export interface ChatDoctorCheck {
  name: string;
  status: string;
  detail: string;
  fix: string;
  doc: string;
}

export interface ChatStatus {
  enabled: boolean;
  backend: string;
  checks: ChatDoctorCheck[];
}

/** One corpus chunk the answer cites; a chip renders it. */
export interface ChatCitation {
  chunk_id: string;
  document_id: string;
  title: string;
  source_path: string;
  snippet: string;
  score: number;
}

/** One settled turn (user or assistant) in the transcript. */
export interface ChatTurn {
  role: "user" | "assistant";
  text: string;
  stopped?: boolean;
  isError?: boolean;
  citations?: ChatCitation[];
  costUsd?: number | null;
  durationMs?: number | null;
  numTurns?: number | null;
  model?: string;
}

interface StreamFlight {
  text: string;
  activity: string | null;
  warning: string | null;
}

const EMPTY_FLIGHT: StreamFlight = { text: "", activity: null, warning: null };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readCitations(envelope: Record<string, unknown>): ChatCitation[] {
  const rows = envelope.citations;
  if (!Array.isArray(rows)) return [];
  return rows.filter(isRecord).map((row) => ({
    chunk_id: String(row.chunk_id ?? ""),
    document_id: String(row.document_id ?? ""),
    title: String(row.title ?? ""),
    source_path: String(row.source_path ?? ""),
    snippet: String(row.snippet ?? ""),
    score: typeof row.score === "number" ? row.score : 0,
  }));
}

export function useChatSession() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [backend, setBackend] = useState<string>("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [flight, setFlight] = useState<StreamFlight | null>(null);
  const [error, setError] = useState<OpResult | null>(null);
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const textRef = useRef("");
  const busy = flight !== null;

  useEffect(() => {
    let live = true;
    void callOp<{ result?: ChatStatus }>("/api/chat/status", { method: "GET" }).then((result) => {
      if (live && result.ok && isRecord(result.data) && isRecord(result.data.result)) {
        setStatus(result.data.result);
      }
    });
    return () => {
      live = false;
    };
  }, []);

  const start = useCallback(async (resume: string | null): Promise<boolean> => {
    setError(null);
    const result = await callOp<{ result?: { session_id: string; backend: string } }>(
      "/api/chat/sessions",
      { method: "POST", body: resume === null ? {} : { resume } },
    );
    const payload = result.ok && isRecord(result.data) ? result.data.result : undefined;
    if (!result.ok || !isRecord(payload)) {
      setError(result);
      return false;
    }
    setSessionId(String(payload.session_id ?? ""));
    setBackend(String(payload.backend ?? ""));
    return true;
  }, []);

  const stop = useCallback((): void => {
    abortRef.current?.abort();
  }, []);

  const send = useCallback(
    async (text: string): Promise<void> => {
      if (busy || sessionId === null || text.trim() === "") return;
      setError(null);
      const clean = text.trim();
      textRef.current = "";
      setTurns((prev) => [...prev, { role: "user", text: clean }]);
      setFlight(EMPTY_FLIGHT);
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const result = await callOpStream(`/api/chat/sessions/${sessionId}/messages`, {
        method: "POST",
        body: { text: clean, stream: true },
        signal: ctrl.signal,
        timeoutMs: "none",
        onDelta: (piece) => {
          textRef.current += piece;
          setFlight((prev) => ({
            ...(prev ?? EMPTY_FLIGHT),
            text: (prev?.text ?? "") + piece,
          }));
        },
        onHouseFrame: (frame) => {
          if (frame.object === "chat.activity") {
            const summary = typeof frame.summary === "string" ? frame.summary : null;
            setFlight((prev) => ({ ...(prev ?? EMPTY_FLIGHT), activity: summary }));
          } else if (frame.object === "chat.warning" && typeof frame.text === "string") {
            const text = frame.text;
            setFlight((prev) => ({ ...(prev ?? EMPTY_FLIGHT), warning: text }));
          } else if (frame.object === "chat.error") {
            setError({
              ok: false,
              kind: "error",
              httpStatus: typeof frame.status === "number" ? frame.status : 500,
              status: "error",
              message: typeof frame.error === "string" ? frame.error : "The turn failed.",
              remediation: "",
              errorType: typeof frame.error_type === "string" ? frame.error_type : "internal_error",
              data: frame,
            });
          }
        },
      });
      abortRef.current = null;
      const streamed = textRef.current;
      setFlight(null);
      if (ctrl.signal.aborted) {
        setTurns((prev) => [...prev, { role: "assistant", text: streamed, stopped: true }]);
        return;
      }
      if (result.ok && isRecord(result.data)) {
        const envelope = result.data;
        setTurns((prev) => [
          ...prev,
          {
            role: "assistant",
            text: streamed,
            citations: readCitations(envelope),
            costUsd: typeof envelope.total_cost_usd === "number" ? envelope.total_cost_usd : null,
            durationMs: typeof envelope.duration_ms === "number" ? envelope.duration_ms : null,
            numTurns: typeof envelope.num_turns === "number" ? envelope.num_turns : null,
            model: typeof envelope.model === "string" ? envelope.model : undefined,
            isError: envelope.is_error === true,
          },
        ]);
        return;
      }
      // The chat.error frame already set the error panel; a network or
      // unreadable result surfaces here.
      if (error === null) {
        setError(result);
      }
    },
    [busy, sessionId, error],
  );

  return {
    sessionId,
    backend,
    turns,
    flight,
    error,
    status,
    busy,
    start,
    send,
    stop,
    setError,
  };
}
