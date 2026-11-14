// useChatSession hook tests (Phase-2 Wave 7): the session lifecycle and
// the settle path over a scripted SSE stream, headless (no page).
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChatSession } from "./useChatSession";
import { clearCalls, respond, respondSSE, stubFetch } from "../test/testUtils";

const ENVELOPE = {
  session_id: "s1",
  backend: "claude-agent-sdk",
  num_turns: 1,
  duration_ms: 100,
  is_error: false,
  terminal_reason: "completed",
  total_cost_usd: 0.01,
  model_usage: {},
  model: "claude-sonnet-5",
  citations: [
    { chunk_id: "c", document_id: "d", title: "t.md", source_path: "", snippet: "s", score: 1 },
  ],
};

const FRAMES = [
  'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n',
  `data: ${JSON.stringify({ object: "chat.envelope", envelope: ENVELOPE })}\n\n`,
  "data: [DONE]\n\n",
];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

describe("useChatSession", () => {
  it("reads the honest status on mount", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/chat/status",
        reply: respond(200, {
          status: "success",
          result: { enabled: false, backend: "claude-agent-sdk", checks: [] },
        }),
      },
    ]);
    const { result } = renderHook(() => useChatSession());
    await waitFor(() => expect(result.current.status?.enabled).toBe(false));
  });

  it("opens a session, sends one turn, settles the receipt", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/chat/status",
        reply: respond(200, {
          status: "success",
          result: { enabled: true, backend: "claude-agent-sdk", checks: [] },
        }),
      },
      {
        method: "POST",
        path: "/api/chat/sessions/s1/messages",
        reply: respondSSE(FRAMES, 10),
      },
      {
        method: "POST",
        path: "/api/chat/sessions",
        reply: respond(200, {
          status: "success",
          result: { session_id: "s1", backend: "claude-agent-sdk" },
        }),
      },
    ]);
    const { result } = renderHook(() => useChatSession());
    await waitFor(() => expect(result.current.status?.enabled).toBe(true));
    await act(async () => {
      expect(await result.current.start(null)).toBe(true);
    });
    expect(result.current.sessionId).toBe("s1");
    await act(async () => {
      await result.current.send("q");
    });
    expect(result.current.turns).toHaveLength(2);
    const assistant = result.current.turns[1];
    expect(assistant.text).toBe("Hi");
    expect(assistant.citations?.[0]?.chunk_id).toBe("c");
    expect(assistant.costUsd).toBeCloseTo(0.01);
    expect(result.current.busy).toBe(false);
  });

  it("a disabled start surfaces the typed error", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/chat/status",
        reply: respond(200, {
          status: "success",
          result: { enabled: false, backend: "claude-agent-sdk", checks: [] },
        }),
      },
      {
        method: "POST",
        path: "/api/chat/sessions",
        reply: respond(400, {
          status: "error",
          error: "Chat is disabled.",
          error_type: "config_error",
        }),
      },
    ]);
    const { result } = renderHook(() => useChatSession());
    await act(async () => {
      expect(await result.current.start(null)).toBe(false);
    });
    expect(result.current.error?.errorType).toBe("config_error");
  });
});
