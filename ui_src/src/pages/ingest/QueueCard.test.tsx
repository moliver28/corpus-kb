import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import {
  calls,
  clearCalls,
  makeWrapper,
  respond,
  stubFetch,
} from "../../test/testUtils";
import type { Call, MinimalResponse, Route } from "../../test/testUtils";
import queueCancelOk from "../../test/fixtures/queue-cancel-ok-200.json";
import queueEnqueueDedup from "../../test/fixtures/queue-enqueue-dedup-200.json";
import queueEnqueueOk from "../../test/fixtures/queue-enqueue-ok-200.json";
import queueStatusOk from "../../test/fixtures/queue-status-ok-200.json";
import QueueCard from "./QueueCard";

/**
 * The queue tab from canonical fixtures: the enqueue form (path gate,
 * client-side strategy-config JSON check), the polled table, and the
 * confirm-gated cancel per active row. The poll is the usePoll
 * precedent, so every timer-driven test runs under fake timers.
 */

const statusRoute: Route = {
  method: "POST",
  path: "/api/queue/status",
  reply: respond(200, queueStatusOk.response.body),
};

/** The enqueue answers deduplicated on its second call (repeat click). */
function stubEnqueueThenDedup(): void {
  let enqueueReads = 0;
  const impl = (url: string | URL, init?: { method?: string; body?: unknown }): Promise<MinimalResponse> => {
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url: String(url), body: String(init?.body ?? "") });
    if (String(url).includes("/api/queue/enqueue")) {
      enqueueReads += 1;
      const body =
        enqueueReads === 1 ? queueEnqueueOk.response.body : queueEnqueueDedup.response.body;
      return Promise.resolve(respond(200, body));
    }
    if (String(url).includes("/api/queue/status")) {
      return Promise.resolve(statusRoute.reply);
    }
    return Promise.reject(new Error(`unexpected fetch: ${method} ${String(url)}`));
  };
  vi.stubGlobal("fetch", impl as unknown as typeof fetch);
}

function queueCalls(path: string): Call[] {
  return calls.filter((c) => c.method === "POST" && c.url.includes(path));
}

function statusCalls(): Call[] {
  return queueCalls("/api/queue/status");
}

function fill(field: string, value: string): void {
  fireEvent.change(screen.getByTestId(field), { target: { value } });
}

function cardOf(title: string): HTMLElement {
  const card = screen
    .getAllByText(title)
    .map((el) => el.closest(".ant-card"))
    .find((c) => c !== null && c.querySelector(".ant-card-head-title")?.textContent === title);
  if (card === undefined) {
    throw new Error(`no card found for ${title}`);
  }
  return card as HTMLElement;
}

// jsdom keeps document.hidden on the Document prototype; a getter spy
// fakes tab visibility, and the event wakes the parked poll loop.
function setTabVisible(visible: boolean): void {
  vi.spyOn(document, "hidden", "get").mockReturnValue(!visible);
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

async function flush(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
  clearCalls();
});

describe("QueueCard", () => {
  it("enqueues one book with its settings and flags a repeat", async () => {
    vi.useFakeTimers();
    setTabVisible(true);
    stubEnqueueThenDedup();
    render(<QueueCard />, { wrapper: makeWrapper() });
    await flush();
    fill("queue-path", "/data/books/networks.pdf");
    fill("queue-title", "A History of Networks");
    fill("queue-profile", "textbook");
    fill("queue-config", '{"window_pages": 25}');
    fireEvent.click(screen.getByTestId("queue-enqueue"));
    await flush();
    expect(screen.getByText(strings.queueEnqueued)).toBeTruthy();
    const sent = queueCalls("/api/queue/enqueue")[0];
    expect(sent?.body).toContain('"path":"/data/books/networks.pdf"');
    expect(sent?.body).toContain('"title":"A History of Networks"');
    expect(sent?.body).toContain('"profile":"textbook"');
    expect(sent?.body).toContain('"strategy_config":{"window_pages":25}');
    // A repeat enqueue answers the existing row; the note says so.
    fireEvent.click(screen.getByTestId("queue-enqueue"));
    await flush();
    expect(screen.getByText(strings.queueDeduplicated)).toBeTruthy();
  });

  it("keeps a malformed strategy config off the wire", async () => {
    vi.useFakeTimers();
    setTabVisible(true);
    stubFetch([statusRoute]);
    render(<QueueCard />, { wrapper: makeWrapper() });
    await flush();
    fill("queue-path", "/data/books/networks.pdf");
    fill("queue-config", "[1]");
    fireEvent.click(screen.getByTestId("queue-enqueue"));
    await flush();
    expect(screen.getByText(strings.queueConfigInvalid)).toBeTruthy();
    expect(queueCalls("/api/queue/enqueue").length).toBe(0);
  });

  it("requires the file path first", async () => {
    vi.useFakeTimers();
    setTabVisible(true);
    stubFetch([statusRoute]);
    render(<QueueCard />, { wrapper: makeWrapper() });
    await flush();
    fireEvent.click(screen.getByTestId("queue-enqueue"));
    await flush();
    expect(screen.getByText(strings.queuePathRequired)).toBeTruthy();
    expect(queueCalls("/api/queue/enqueue").length).toBe(0);
  });

  it("renders the queue rows and cancels an active entry after its confirm", async () => {
    stubFetch([
      statusRoute,
      {
        method: "POST",
        path: "/api/queue/cancel",
        reply: respond(200, queueCancelOk.response.body),
      },
    ]);
    render(<QueueCard />, { wrapper: makeWrapper() });
    const tableCard = cardOf(strings.queueTableTitle);
    await within(tableCard).findByText("A History of Networks", undefined, { timeout: 4000 });
    expect(within(tableCard).getByText("25 of 50 pages; 12 chunks")).toBeTruthy();
    expect(within(tableCard).getByText("1 of 3")).toBeTruthy();
    expect(within(tableCard).getByText("1841 s")).toBeTruthy();
    // The pending row has no title, so its path stands in; no progress.
    expect(within(tableCard).getByText("/data/books/atlases.pdf")).toBeTruthy();
    expect(within(tableCard).getAllByText("-").length).toBeGreaterThan(0);
    // Cancel opens the per-row confirm; only the popover button cancels.
    fireEvent.click(screen.getByTestId("queue-cancel-dddd3333-4444-4e55-8f66-77778888aaaa"));
    const pop = await screen.findByText(strings.queueCancelConfirmTitle, undefined, {
      timeout: 4000,
    });
    const popover = pop.closest(".ant-popover");
    expect(popover).not.toBeNull();
    fireEvent.click(
      within(popover as HTMLElement).getByRole("button", { name: strings.queueCancelConfirmOk }),
    );
    expect(
      await screen.findByText(strings.queueCancelDone, undefined, { timeout: 4000 }),
    ).toBeTruthy();
    const sent = queueCalls("/api/queue/cancel")[0];
    expect(sent?.body).toContain('"queue_id":"dddd3333-4444-4e55-8f66-77778888aaaa"');
  });

  it("polls on the runtime interval, pauses when hidden, and refreshes after an enqueue", async () => {
    vi.useFakeTimers();
    setTabVisible(true);
    stubEnqueueThenDedup();
    render(<QueueCard />, { wrapper: makeWrapper() });
    await flush();
    expect(statusCalls().length).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(statusCalls().length).toBe(2);
    // The enqueue kicks one immediate refresh — no wait for the tick.
    fill("queue-path", "/data/books/networks.pdf");
    fireEvent.click(screen.getByTestId("queue-enqueue"));
    await flush();
    expect(queueCalls("/api/queue/enqueue").length).toBe(1);
    expect(statusCalls().length).toBe(3);
    // Hidden: the loop parks on the visibilitychange wake — no fetches.
    setTabVisible(false);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(statusCalls().length).toBe(3);
    expect(screen.getByText(strings.queuePaused)).toBeTruthy();
    setTabVisible(true);
    await flush();
    expect(statusCalls().length).toBe(4);
  });
});
