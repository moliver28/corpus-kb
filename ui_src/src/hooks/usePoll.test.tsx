import { act, cleanup, render } from "@testing-library/react";
import { useEffect } from "react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import { subscribePoll } from "./pollCache";
import { usePoll } from "./usePoll";
import type { Poll } from "./usePoll";

/**
 * The shared poll cache: N consumers of one op ride ONE network loop.
 * The carry-forward (#324 item 2) proof — two mounted consumers of the
 * same op must produce one fetch per tick, a refresh from both must
 * kick one extra read, the loop survives one consumer's unmount, and
 * the last unmount stops it. Different ops keep separate loops.
 */

// The latest settled poll state per probe, reported outside render.
const latest = new Map<string, Poll<unknown>>();

function Probe({ opId, tag }: { opId: string; tag: string }) {
  const poll = usePoll(opId);
  useEffect(() => {
    latest.set(tag, poll);
  });
  return (
    <span>
      {poll.result === null ? "wait" : poll.result.ok ? "ok" : "down"}
      {poll.paused ? " paused" : ""}
    </span>
  );
}

function callsTo(path: string): number {
  return calls.filter((c) => c.url.includes(path)).length;
}

async function flush(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  clearCalls();
  latest.clear();
});

describe("the shared poll cache", () => {
  it("runs one network loop for two mounted consumers of one op", async () => {
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, { status: "success", result: [] }) },
      { method: "GET", path: "/api/doctor", reply: respond(200, { status: "success", result: [] }) },
    ]);
    // The poll loop reads document.hidden at mount; the jsdom default is
    // a visible tab, so no spy is needed here.
    render(
      <>
        <Probe opId="ingest-status" tag="a" />
        <Probe opId="ingest-status" tag="b" />
      </>,
      { wrapper: makeWrapper(false) },
    );
    await flush();
    // One first read total — the shared loop, not one per consumer.
    expect(callsTo("/api/ingest/status")).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    // One more tick = exactly one more fetch, not three.
    expect(callsTo("/api/ingest/status")).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(callsTo("/api/ingest/status")).toBe(3);
  });

  it("delivers the same answer to both consumers", async () => {
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, { status: "success", result: [] }) },
      { method: "GET", path: "/api/doctor", reply: respond(200, { status: "success", result: [] }) },
    ]);
    render(
      <>
        <Probe opId="ingest-status" tag="a" />
        <Probe opId="ingest-status" tag="b" />
      </>,
      { wrapper: makeWrapper(false) },
    );
    await flush();
    expect(latest.get("a")?.result?.ok).toBe(true);
    expect(latest.get("b")?.result?.ok).toBe(true);
    expect(latest.get("a")?.result).toBe(latest.get("b")?.result);
  });

  it("keeps polling after one consumer unmounts and stops after the last", async () => {
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, { status: "success", result: [] }) },
      { method: "GET", path: "/api/doctor", reply: respond(200, { status: "success", result: [] }) },
    ]);
    const first = render(<Probe opId="ingest-status" tag="a" />, { wrapper: makeWrapper(false) });
    render(<Probe opId="ingest-status" tag="b" />, { wrapper: makeWrapper(false) });
    await flush();
    expect(callsTo("/api/ingest/status")).toBe(1);
    // One consumer leaves; the loop lives for the other.
    first.unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(callsTo("/api/ingest/status")).toBe(2);
    cleanup();
    // The last subscriber left: the loop stops, no further fetches.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(callsTo("/api/ingest/status")).toBe(2);
  });

  it("kicks one extra read when both consumers refresh", async () => {
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, { status: "success", result: [] }) },
      { method: "GET", path: "/api/doctor", reply: respond(200, { status: "success", result: [] }) },
    ]);
    render(
      <>
        <Probe opId="ingest-status" tag="a" />
        <Probe opId="ingest-status" tag="b" />
      </>,
      { wrapper: makeWrapper(false) },
    );
    await flush();
    expect(callsTo("/api/ingest/status")).toBe(1);
    await act(async () => {
      latest.get("a")?.refresh();
      latest.get("b")?.refresh();
    });
    await flush();
    // Single-flight dirty: both kicks answer with ONE extra read.
    expect(callsTo("/api/ingest/status")).toBe(2);
  });

  it("keeps separate loops for different ops", async () => {
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, { status: "success", result: [] }) },
      { method: "GET", path: "/api/doctor", reply: respond(200, { status: "success", result: [] }) },
    ]);
    render(
      <>
        <Probe opId="ingest-status" tag="a" />
        <Probe opId="doctor" tag="b" />
      </>,
      { wrapper: makeWrapper(false) },
    );
    await flush();
    expect(callsTo("/api/ingest/status")).toBe(1);
    expect(callsTo("/api/doctor")).toBe(1);
  });

  it("sends the poll body on POST ops (the bodyRef contract)", async () => {
    // P2-1 (Wave-4 review): subscribePoll must write the body into the
    // loop before the first tick, or the first POST poll loses it.
    vi.useFakeTimers();
    stubFetch([
      { method: "POST", path: "/api/graph/search", reply: respond(200, { status: "success", result: [] }) },
    ]);
    function BodyProbe(): ReactNode {
      const poll = usePoll<unknown>("search-graph", { query: "abc" });
      useEffect(() => {
        latest.set("body", poll);
      });
      return <span>{poll.result === null ? "wait" : poll.result.ok ? "ok" : "down"}</span>;
    }
    render(<BodyProbe />, { wrapper: makeWrapper(false) });
    await flush();
    expect(latest.get("body")?.result?.ok).toBe(true);
    const posted = calls.find((c) => c.method === "POST" && c.url.includes("/api/graph/search"));
    expect(posted).toBeDefined();
    expect(posted?.body).toContain('"query":"abc"');
  });

  it("does not re-render consumers when a tick changes nothing", async () => {
    // P2-2 (Wave-4 review): identical polls and the per-pass paused /
    // backoff patches keep the old snapshot, so unchanged consumers do
    // not re-render 2-3x per tick.
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, { status: "success", result: [] }) },
    ]);
    let renders = 0;
    function CountProbe(): ReactNode {
      const poll = usePoll("ingest-status");
      renders += 1;
      useEffect(() => {
        latest.set("count", poll);
      });
      return <span>{poll.result === null ? "wait" : poll.result.ok ? "ok" : "down"}</span>;
    }
    render(<CountProbe />, { wrapper: makeWrapper(false) });
    await flush();
    const afterFirst = renders;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4500); // two more ticks, same answer
    });
    expect(callsTo("/api/ingest/status")).toBe(3);
    // Render one (mount) + render two (the first real answer); the two
    // identical ticks add none.
    expect(afterFirst).toBe(2);
    expect(renders).toBe(2);
  });

  it("backs off ten seconds after a 503 and says so", async () => {
    // P2-5 (Wave-4 review): the loop's failure paths moved into
    // pollCache.ts with the wave; each arm below pins one of them.
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(503, { status: "unavailable" }) },
    ]);
    render(<Probe opId="ingest-status" tag="a" />, { wrapper: makeWrapper(false) });
    await flush();
    expect(callsTo("/api/ingest/status")).toBe(1);
    expect(latest.get("a")?.backingOff).toBe(true);
    // The next pass waits out the backoff, not the regular interval.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(callsTo("/api/ingest/status")).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(callsTo("/api/ingest/status")).toBe(2);
  });

  it("keeps polling after an op error lands mid-poll", async () => {
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(500, { status: "error", error: "boom" }) },
    ]);
    render(<Probe opId="ingest-status" tag="a" />, { wrapper: makeWrapper(false) });
    await flush();
    expect(latest.get("a")?.result?.ok).toBe(false);
    expect(latest.get("a")?.result?.kind).toBe("error");
    // An error answer parks nothing: the loop stays on its cadence.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(callsTo("/api/ingest/status")).toBe(2);
  });

  it("parks while the tab is hidden and wakes on visibility", async () => {
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, { status: "success", result: [] }) },
    ]);
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    render(<Probe opId="ingest-status" tag="a" />, { wrapper: makeWrapper(false) });
    await flush();
    // Hidden: no fetch goes out, and the consumer knows it is paused.
    expect(callsTo("/api/ingest/status")).toBe(0);
    expect(latest.get("a")?.paused).toBe(true);
    hidden.mockReturnValue(false);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(callsTo("/api/ingest/status")).toBe(1);
    expect(latest.get("a")?.paused).toBe(false);
  });

  it("serves two joins with one loop and stops on the last release", async () => {
    vi.useFakeTimers();
    stubFetch([
      { method: "GET", path: "/api/ingest/status", reply: respond(200, { status: "success", result: [] }) },
    ]);
    const seen: unknown[] = [];
    const first = subscribePoll("ingest-status", undefined, (s) => seen.push(s));
    const second = subscribePoll("ingest-status", undefined, (s) => seen.push(s));
    await flush();
    // One fetch for both joins; the no-op gate means each join saw
    // exactly the one answer (the per-pass paused patch never applies).
    expect(callsTo("/api/ingest/status")).toBe(1);
    expect(seen.length).toBe(2);
    first.release();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    // The loop lives for the remaining join.
    expect(callsTo("/api/ingest/status")).toBe(2);
    second.release();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    // The last release stopped it.
    expect(callsTo("/api/ingest/status")).toBe(2);
  });
});
