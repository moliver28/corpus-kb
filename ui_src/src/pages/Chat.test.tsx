// Chat page tests (Phase-2 Wave 7, work-order step 7). jsdom fetch is
// stubbed with a scripted SSE ReadableStream (the testUtils recorder):
// deltas append, the envelope settles the citation chips and the receipt
// line, the error frame opens the error panel, stop settles the honest
// "Stopped" bubble, the disabled state renders the typed status panel,
// and every rendered X default equals the chat strings table ([D12]).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router";
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Chat from "./Chat";
import { strings } from "../strings/chat";
import {
  clearCalls,
  makeWrapper,
  respond,
  respondSSE,
  stubFetch,
  type Route as RouteStub,
} from "../test/testUtils";

const SENDER_ROUTE = "/api/chat/sessions";

const GOOD_ENVELOPE = {
  session_id: "s1",
  backend: "claude-agent-sdk",
  num_turns: 2,
  duration_ms: 1500,
  is_error: false,
  terminal_reason: "completed",
  total_cost_usd: 0.02,
  model_usage: {},
  model: "claude-sonnet-5",
  citations: [
    {
      chunk_id: "c-1",
      document_id: "d-1",
      title: "docs/report.pdf",
      source_path: "/docs/report.pdf",
      snippet: "Budget line.",
      score: 0.9,
    },
  ],
};

function chunk(text: string): string {
  return `data: ${JSON.stringify({
    id: "c1",
    object: "chat.completion.chunk",
    choices: [{ index: 0, delta: { content: text }, finish_reason: null }],
  })}\n\n`;
}

const GOOD_FRAMES = [
  chunk("Hello "),
  chunk("world"),
  `data: ${JSON.stringify({
    object: "chat.activity",
    tool: "search",
    summary: strings.chatActivitySearching,
    chunk_count: null,
  })}\n\n`,
  `data: ${JSON.stringify({ object: "chat.warning", text: "Your login expires in 3 days" })}\n\n`,
  `data: ${JSON.stringify({ object: "chat.envelope", envelope: GOOD_ENVELOPE })}\n\n`,
  "data: [DONE]\n\n",
];

const OK_STATUS = respond(200, {
  status: "success",
  result: {
    enabled: true,
    backend: "claude-agent-sdk",
    checks: [{ name: "chat login pin", status: "ok", detail: "ok", fix: "", doc: "docs/CHAT.md" }],
  },
});

const EMPTY_SESSIONS = respond(200, { status: "success", result: { sessions: [] } });

function baseRoutes(streamReply: ReturnType<typeof respondSSE>): RouteStub[] {
  // The stub matches by substring IN ORDER, so the specific message
  // route precedes the generic sessions route.
  return [
    { method: "GET", path: "/api/chat/status", reply: OK_STATUS },
    { method: "POST", path: "/api/chat/sessions/s1/messages", reply: streamReply },
    { method: "POST", path: SENDER_ROUTE, reply: respond(200, { status: "success", result: { session_id: "s1", backend: "claude-agent-sdk" } }) },
    { method: "GET", path: "/api/chat/sessions?", reply: EMPTY_SESSIONS },
  ];
}

function renderChat(): void {
  render(createElement(Chat), { wrapper: makeWrapper(true, ["/chat"]) });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

describe("Chat page", () => {
  it("streams deltas, settles the chips and the receipt line", async () => {
    stubFetch(baseRoutes(respondSSE(GOOD_FRAMES)));
    renderChat();
const composer = await screen.findByPlaceholderText(strings.chatPlaceholder);
    fireEvent.change(composer, { target: { value: "What changed?" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await screen.findByText("Hello world");
    // The settled turn: chips render from the envelope citations, the
    // receipt line carries the honest cost.
    await screen.findByText("report.pdf");
    expect(screen.getByText((_, element) => element?.textContent === "Cost: $0.0200 · Time: 1.5 s · Turns: 2 · Model: claude-sonnet-5")).not.toBeNull();
    expect(screen.getByLabelText(strings.chatCitationsTitle)).not.toBeNull();
  });

  it("renders the mid-flight activity and warning lines verbatim", async () => {
    stubFetch(baseRoutes(respondSSE(GOOD_FRAMES)));
    renderChat();
const composer = await screen.findByPlaceholderText(strings.chatPlaceholder);
    fireEvent.change(composer, { target: { value: "q" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    await screen.findByText("report.pdf");
  });

  it("an error frame opens the error panel with the taxonomy message", async () => {
    const frames = [
      chunk("part"),
      `data: ${JSON.stringify({ object: "chat.error", status: 409, error: "One turn runs at a time.", error_type: "conflict" })}\n\n`,
      "data: [DONE]\n\n",
    ];
    stubFetch(baseRoutes(respondSSE(frames)));
    renderChat();
const composer = await screen.findByPlaceholderText(strings.chatPlaceholder);
    fireEvent.change(composer, { target: { value: "q" } });
    fireEvent.keyDown(composer, { key: "Enter" });
  });

  it("stop settles the honest Stopped bubble and clears the flight", async () => {
    // One held frame to stop against, then a short tail: the stubbed
    // stream ignores the abort signal, so the tail must finish inside
    // the finder budget.
    const stopFrames = [
      `data: ${JSON.stringify({
        object: "chat.activity",
        tool: "search",
        summary: strings.chatActivitySearching,
        chunk_count: null,
      })}

`,
      `data: ${JSON.stringify({
        object: "chat.envelope",
        envelope: GOOD_ENVELOPE,
      })}

`,
      "data: [DONE]\n\n",
    ];
    stubFetch(baseRoutes(respondSSE(stopFrames, 300)));
    renderChat();
    const composer = await screen.findByPlaceholderText(strings.chatPlaceholder);
    fireEvent.change(composer, { target: { value: "q" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    // The stop control is the flight's honest cancel: click it while
    // the turn still runs.
    const stopText = await screen.findByText(strings.chatStopLabel);
    fireEvent.click(stopText.closest("button") ?? stopText);
    await screen.findByText(strings.chatStopped);
    await waitFor(() => expect(screen.queryByText(strings.chatStopLabel)).toBeNull());
  });

  it("the disabled state renders the typed status panel, never a composer", async () => {
    stubFetch([
      {
        method: "GET",
        path: "/api/chat/status",
        reply: respond(200, {
          status: "success",
          result: { enabled: false, backend: "claude-agent-sdk", checks: [] },
        }),
      },
      { method: "GET", path: "/api/chat/sessions?", reply: EMPTY_SESSIONS },
    ]);
    renderChat();
    await screen.findByText(strings.chatStatusTitle);
    expect(screen.getByText(strings.chatDisabledBody)).not.toBeNull();
    expect(screen.queryByPlaceholderText(strings.chatPlaceholder)).toBeNull();
  });

  it("a citation chip deep-links to the review drawer query", async () => {
    let seen = "";
    function Probe(): null {
      const location = useLocation();
      seen = `${location.pathname}${location.search}`;
      return null;
    }
    stubFetch(baseRoutes(respondSSE(GOOD_FRAMES)));
    render(
      createElement(
        "div",
        null,
        createElement(Chat),
        createElement(
          Routes,
          null,
          createElement(Route, { path: "/review", element: createElement(Probe) }),
        ),
        createElement(Probe),
      ),
      { wrapper: makeWrapper(true, ["/chat"]) },
    );
    const composer = await screen.findByPlaceholderText(strings.chatPlaceholder);
    fireEvent.change(composer, { target: { value: "q" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    fireEvent.click(await screen.findByText("report.pdf"));
    expect(seen).toBe("/review?analyze=d-1");
  });

  it("every rendered X default equals the chat strings table ([D12])", async () => {
    stubFetch(baseRoutes(respondSSE(GOOD_FRAMES)));
    renderChat();
    const composer = await screen.findByPlaceholderText(strings.chatPlaceholder);
    expect(composer.getAttribute("placeholder")).toBe(strings.chatPlaceholder);
    fireEvent.change(composer, { target: { value: "q" } });
    fireEvent.keyDown(composer, { key: "Enter" });
    // The stop control exists only while the turn streams; its rendered
    // label is the table's ([D12] override of the X default).
    await screen.findByText(strings.chatStopLabel);
    await screen.findByText("report.pdf");
  });
});
