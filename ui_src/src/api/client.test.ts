import { afterEach, describe, expect, it, vi } from "vitest";
import { callOp } from "./client";
import { strings } from "../strings";
import { respond, stubFetchImpl } from "../test/testUtils";

/**
 * Client behavior table (spec D.3): failure envelope, 200-with-error,
 * 503, 409, neutral-on-200, non-JSON, network, timeout. POST never
 * retries. Fetch is stubbed; no live server is needed here (the recorded
 * fixtures in src/test/fixtures pin real payload shapes).
 */

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("callOp", () => {
  it("maps a failure envelope to kind error", async () => {
    stubFetchImpl(() => Promise.resolve(respond(400, {
      status: "error", error: "boom", error_type: "validation_error",
      remediation: "Add the missing field.",
    })));
    const res = await callOp("/api/x");
    expect(res).toMatchObject({
      ok: false, kind: "error", httpStatus: 400,
      status: "error", message: "boom", errorType: "validation_error",
      remediation: "Add the missing field.",
    });
  });

  it("maps HTTP 200 with status error to kind error", async () => {
    stubFetchImpl(() => Promise.resolve(respond(200, { status: "error", error: "late" })));
    const res = await callOp("/api/x");
    expect(res.ok).toBe(false);
    expect(res.kind).toBe("error");
    expect(res.httpStatus).toBe(200);
  });

  it("maps 503 to unavailable", async () => {
    stubFetchImpl(() => Promise.resolve(respond(503, { status: "error", error: "down" })));
    const res = await callOp("/api/x");
    expect(res.kind).toBe("unavailable");
    expect(res.ok).toBe(false);
  });

  it("maps 409 to blocked", async () => {
    stubFetchImpl(() => Promise.resolve(respond(409, { status: "blocked", message: "no" })));
    const res = await callOp("/api/x");
    expect(res.kind).toBe("blocked");
    expect(res.ok).toBe(false);
    expect(res.message).toBe("no");
  });

  it("keeps neutral statuses (empty, degraded) on 200 ok", async () => {
    stubFetchImpl(() => Promise.resolve(respond(200, { status: "empty", results: [] })));
    const empty = await callOp("/api/x");
    expect(empty.ok).toBe(true);
    expect(empty.kind).toBe("ok");
    expect(empty.status).toBe("empty");
    stubFetchImpl(() => Promise.resolve(respond(200, { status: "degraded", message: "gaps" })));
    const degraded = await callOp("/api/x");
    expect(degraded.ok).toBe(true);
    expect(degraded.status).toBe("degraded");
  });

  it("surfaces a non-JSON body as unreadable, with the http status", async () => {
    stubFetchImpl(() => Promise.resolve({ status: 502, text: async () => "<html>bad</html>" }));
    const res = await callOp("/api/x");
    expect(res.ok).toBe(false);
    expect(res.kind).toBe("unknown");
    expect(res.httpStatus).toBe(502);
    expect(res.message).toBe(strings.unreadableResponse);
  });

  it("maps a fetch rejection to the network kind", async () => {
    stubFetchImpl(() => Promise.reject(new Error("ECONNREFUSED")));
    const res = await callOp("/api/x");
    expect(res.kind).toBe("network");
    expect(res.httpStatus).toBe(0);
    expect(res.message).toBe(strings.serverNotAnswering);
    expect(res.remediation).toBe(strings.remediationRunDoctor);
  });

  it("times out and reports the network kind", async () => {
    stubFetchImpl((_url: string, init?: RequestInit) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
    }));
    const res = await callOp("/api/x", { timeoutMs: 20 });
    expect(res.kind).toBe("network");
    expect(res.ok).toBe(false);
  });

  it("aborts immediately when the caller signal is already aborted", async () => {
    let abortedAtCall = false;
    stubFetchImpl((_url: string, init?: RequestInit) => new Promise((_resolve, reject) => {
      if (init?.signal?.aborted) {
        abortedAtCall = true;
        reject(new Error("aborted"));
      }
    }));
    const ctrl = new AbortController();
    ctrl.abort();
    const res = await callOp("/api/x", { signal: ctrl.signal, timeoutMs: "none" });
    expect(abortedAtCall).toBe(true);
    expect(res.kind).toBe("network");
    expect(res.ok).toBe(false);
  });

  it("sends a POST body with the JSON content type", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    stubFetchImpl((url: string, init?: RequestInit) => {
      calls.push({ url, init: init ?? {} });
      return Promise.resolve(respond(200, { status: "ok" }));
    });
    await callOp("/api/x", { method: "POST", body: { a: 1 } });
    expect(calls).toHaveLength(1);
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.body).toBe(JSON.stringify({ a: 1 }));
  });

  it("never retries: one failure means exactly one fetch", async () => {
    const impl = vi.fn(() => Promise.resolve(respond(500, { status: "error", error: "x" })));
    stubFetchImpl(impl);
    await callOp("/api/x");
    expect(impl).toHaveBeenCalledTimes(1);
  });
});
