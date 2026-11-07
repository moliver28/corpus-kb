// The one shared poll loop per op (carry-forward #324 item 2): N
// consumers of one op (same id, same body) ride ONE network loop, so a
// second card never doubles the polling load. The loop is the former
// per-hook effect body (hooks/usePoll.ts), lifted to module scope:
// poll every runtimeConfig().pollMs while the tab is visible, park on a
// visibilitychange wake while hidden, back off 10 s after a 503, and
// re-poll at once after a refresh kick (single-flight: both consumers'
// kicks answer with ONE extra read). One loop, no parallel polls. The
// op table (api/ops) is the one endpoint source.
import { callOp } from "../api/client";
import { getOp } from "../api/ops";
import { runtimeConfig } from "../api/runtimeConfig";
import type { OpResult } from "../api/types";

const POLL_BACKOFF_MS = 10_000;

/** The state every consumer of one loop mirrors. */
export interface PollSnapshot<T> {
  /** The most recent answer (null until the first poll lands). */
  result: OpResult<T> | null;
  /** True while the browser tab is hidden (no fetches go out). */
  paused: boolean;
  /** True after a 503, until a poll answers again. */
  backingOff: boolean;
}

interface Subscriber {
  apply(snapshot: PollSnapshot<unknown>): void;
}

interface Loop {
  subscribers: Set<Subscriber>;
  /** The loop reads the latest body without restarting on a new object. */
  bodyRef: { current: unknown };
  dirty: boolean;
  wake: (() => void) | null;
  stopped: boolean;
  /** The last payload seen by THIS loop; equal polls never broadcast. */
  lastPayload: string | null;
  /** The state the next subscriber inherits at join. */
  snapshot: PollSnapshot<unknown> | null;
  /** One immediate kick; safe any time, never mid-poll. */
  refresh(): void;
}

const loops = new Map<string, Loop>();

/** Same op + same body content = one loop (bodies are literals here). */
function loopKey(opId: string, body: unknown): string {
  return `${opId}\u0000${JSON.stringify(body ?? null)}`;
}

function broadcast(loop: Loop, patch: Partial<PollSnapshot<unknown>>): void {
  const base = loop.snapshot ?? { result: null, paused: false, backingOff: false };
  const next: PollSnapshot<unknown> = { ...base, ...patch };
  loop.snapshot = next;
  for (const sub of loop.subscribers) {
    sub.apply(next);
  }
}

/**
 * One shared loop for one op. Returns the release call; when the last
 * subscriber releases, the loop stops and its key is dropped, so a
 * later subscriber starts a fresh loop with an immediate first read.
 */
function startLoop(opId: string): Loop {
  const op = getOp(opId);
  const loop: Loop = {
    subscribers: new Set(),
    bodyRef: { current: undefined },
    dirty: false,
    wake: null,
    stopped: false,
    lastPayload: null,
    snapshot: null,
    refresh: () => {
      loop.dirty = true;
      loop.wake?.();
    },
  };
  if (op === undefined) {
    return loop; // an unknown op id never fetches (the honest no-op)
  }
  const sleepOrWake = (ms: number): Promise<void> =>
    new Promise<void>((resolve) => {
      const timer = window.setTimeout(done, ms);
      function done(): void {
        loop.wake = null;
        window.clearTimeout(timer);
        resolve();
      }
      loop.wake = () => done();
    });
  // The hidden park: the loop awaits this promise until the tab
  // becomes visible again — a visibilitychange wake, not a timer.
  const untilVisible = () =>
    new Promise<void>((resolve) => {
      const onChange = () => {
        if (loop.stopped) {
          document.removeEventListener("visibilitychange", onChange);
          return;
        }
        if (!document.hidden) {
          document.removeEventListener("visibilitychange", onChange);
          resolve();
        }
      };
      document.addEventListener("visibilitychange", onChange);
    });
  void (async () => {
    while (!loop.stopped) {
      if (document.hidden) {
        broadcast(loop, { paused: true });
        await untilVisible();
        if (loop.stopped) {
          return;
        }
      }
      broadcast(loop, { paused: false });
      // A poll reads the freshest state there is, so a refresh that
      // fired before this moment is answered by THIS read, not another.
      loop.dirty = false;
      const res = await callOp<unknown>(op.path, {
        method: op.method,
        body: loop.bodyRef.current,
      });
      if (loop.stopped) {
        return;
      }
      const payload = JSON.stringify(res);
      if (payload !== loop.lastPayload) {
        loop.lastPayload = payload;
        broadcast(loop, { result: res });
      }
      const backoff = res.httpStatus === 503 || res.kind === "unavailable";
      broadcast(loop, { backingOff: backoff });
      // A refresh during the read asks for one more pass right away.
      if (loop.dirty) {
        continue;
      }
      await sleepOrWake(backoff ? POLL_BACKOFF_MS : runtimeConfig().pollMs);
    }
  })();
  return loop;
}

/**
 * Join (or start) the one loop for this op + body. The apply callback
 * receives every state change; a joiner inherits the loop's current
 * snapshot at once, so a late card shows the known answer immediately.
 */
export function subscribePoll<T>(
  opId: string,
  body: unknown,
  apply: (snapshot: PollSnapshot<T>) => void,
): { release(): void; refresh(): void } {
  const key = loopKey(opId, body);
  let loop = loops.get(key);
  if (loop === undefined) {
    loop = startLoop(opId);
    loops.set(key, loop);
  }
  const sub: Subscriber = { apply: apply as (snapshot: PollSnapshot<unknown>) => void };
  loop.subscribers.add(sub);
  if (loop.snapshot !== null) {
    apply(loop.snapshot as PollSnapshot<T>);
  }
  const joined = loop;
  return {
    release() {
      joined.subscribers.delete(sub);
      if (joined.subscribers.size === 0) {
        joined.stopped = true;
        joined.wake?.();
        loops.delete(key);
      }
    },
    refresh: joined.refresh,
  };
}
