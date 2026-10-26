import { afterEach, describe, expect, it, vi } from "vitest";
import { callOp } from "./client";
import { strings } from "../strings";

/**
 * Client behavior table (spec D.3): failure envelope, 200-with-error,
 * 503, 409, neutral-on-200, non-JSON, network, timeout. POST never
 * retries. Fetch is stubbed; no live server is needed here (the recorded
 * fixtures in src/test/fixtures pin real payload shapes).
 */

type MinimalResponse = { status: number; text: () => Promise<string> };
type FetchLike = (url: string, init?: RequestInit) => Promise<MinimalResponse>;

function stubFetch(impl: FetchLike): void {
  vi.stubGlobal("fetch", impl as unknown as typeof fetch);
}

function jsonResponse(status: number, body: unknown): MinimalResponse {
  return { status, text: async () => JSON.stringify(body) };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("callOp", () => {
  it("maps a failure envelope to kind error", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(400, {
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
    stubFetch(() => Promise.resolve(jsonResponse(200, { status: "error", error: "late" })));
    const res = await callOp("/api/x");
    expect(res.ok).toBe(false);
    expect(res.kind).toBe("error");
    expect(res.httpStatus).toBe(200);
  });

  it("maps 503 to unavailable", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(503, { status: "error", error: "down" })));
    const res = await callOp("/api/x");
    expect(res.kind).toBe("unavailable");
    expect(res.ok).toBe(false);
  });

  it("maps 409 to blocked", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(409, { status: "blocked", message: "no" })));
    const res = await callOp("/api/x");
    expect(res.kind).toBe("blocked");
    expect(res.ok).toBe(false);
    expect(res.message).toBe("no");
  });

  it("keeps neutral statuses (empty, degraded) on 200 ok", async () => {
    stubFetch(() => Promise.resolve(jsonResponse(200, { status: "empty", results: [] })));
    const empty = await callOp("/api/x");
    expect(empty.ok).toBe(true);
    expect(empty.kind).toBe("ok");
    expect(empty.status).toBe("empty");
    stubFetch(() => Promise.resolve(jsonResponse(200, { status: "degraded", message: "gaps" })));
    const degraded = await callOp("/api/x");
    expect(degraded.ok).toBe(true);
    expect(degraded.status).toBe("degraded");
  });

  it("surfaces a non-JSON body as unreadable, with the http status", async () => {
    stubFetch(() => Promise.resolve({ status: 502, text: async () => "<html>bad</html>" }));
    const res = await callOp("/api/x");
    expect(res.ok).toBe(false);
    expect(res.kind).toBe("unknown");
    expect(res.httpStatus).toBe(502);
    expect(res.message).toBe(strings.unreadableResponse);
  });

  it("maps a fetch rejection to the network kind", async () => {
    stubFetch(() => Promise.reject(new Error("ECONNREFUSED")));
    const res = await callOp("/api/x");
    expect(res.kind).toBe("network");
    expect(res.httpStatus).toBe(0);
    expect(res.message).toBe(strings.serverNotAnswering);
    expect(res.remediation).toBe(strings.remediationRunDoctor);
  });

  it("times out and reports the network kind", async () => {
    stubFetch((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
    }));
    const res = await callOp("/api/x", { timeoutMs: 20 });
    expect(res.kind).toBe("network");
    expect(res.ok).toBe(false);
  });

  it("sends a POST body with the JSON content type", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    stubFetch((url, init) => {
      calls.push({ url, init: init ?? {} });
      return Promise.resolve(jsonResponse(200, { status: "ok" }));
    });
    await callOp("/api/x", { method: "POST", body: { a: 1 } });
    expect(calls).toHaveLength(1);
    expect(calls[0].init.method).toBe("POST");
    expect(calls[0].init.body).toBe(JSON.stringify({ a: 1 }));
  });

  it("never retries: one failure means exactly one fetch", async () => {
    const impl = vi.fn(() => Promise.resolve(jsonResponse(500, { status: "error", error: "x" })));
    stubFetch(impl as unknown as FetchLike);
    await callOp("/api/x");
    expect(impl).toHaveBeenCalledTimes(1);
  });
});
