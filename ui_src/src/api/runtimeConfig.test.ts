import { afterEach, describe, expect, it, vi } from "vitest";
import { stubFetchImpl } from "../test/testUtils";

/**
 * Runtime-config rows: one fetch, cached; every invalid or missing value
 * degrades to the built-in defaults (they mirror corpus_kb.config). The
 * typeguard rows below pin the degrade path — a bad key never crashes
 * the SPA and never changes policy silently.
 */

function jsonBody(body: unknown): unknown {
  return { json: async () => body };
}

async function fresh() {
  vi.resetModules();
  return await import("./runtimeConfig");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("runtimeConfig", () => {
  it("maps the wire keys onto the typed snapshot", async () => {
    const rc = await fresh();
    stubFetchImpl(() =>
      Promise.resolve(
        jsonBody({
          poll_ms: 1500,
          request_timeout_s: 90,
          max_upload_mb: 25,
          server_version: "1.2.3",
        }),
      ),
    );
    await rc.loadRuntimeConfig();
    expect(rc.runtimeConfig()).toEqual({
      pollMs: 1500,
      requestTimeoutS: 90,
      maxUploadMb: 25,
      serverVersion: "1.2.3",
    });
    expect(rc.requestTimeoutMs()).toBe(90_000);
  });

  it("keeps the defaults when the fetch fails", async () => {
    const rc = await fresh();
    stubFetchImpl(() => Promise.reject(new Error("down")));
    await rc.loadRuntimeConfig();
    expect(rc.runtimeConfig()).toEqual({
      pollMs: 2000,
      requestTimeoutS: 60,
      maxUploadMb: 50,
      serverVersion: "",
    });
    expect(rc.requestTimeoutMs()).toBe(60_000);
  });

  it("keeps the defaults when the body is not an object", async () => {
    const rc = await fresh();
    stubFetchImpl(() => Promise.resolve(jsonBody("nope")));
    await rc.loadRuntimeConfig();
    expect(rc.requestTimeoutMs()).toBe(60_000);
  });

  it("degrades per key when a value has the wrong type or range", async () => {
    const rc = await fresh();
    stubFetchImpl(() =>
      Promise.resolve(
        jsonBody({
          poll_ms: "fast",
          request_timeout_s: -5,
          max_upload_mb: 10,
          server_version: 42,
        }),
      ),
    );
    await rc.loadRuntimeConfig();
    expect(rc.runtimeConfig()).toEqual({
      pollMs: 2000,
      requestTimeoutS: 60,
      maxUploadMb: 10,
      serverVersion: "",
    });
  });

  it("fetches once and reuses the cache afterwards", async () => {
    const rc = await fresh();
    const impl = vi.fn(() => Promise.resolve(jsonBody({ request_timeout_s: 30 })));
    stubFetchImpl(impl);
    await rc.loadRuntimeConfig();
    await rc.loadRuntimeConfig();
    expect(impl).toHaveBeenCalledTimes(1);
    expect(rc.requestTimeoutMs()).toBe(30_000);
  });
});
