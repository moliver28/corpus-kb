import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../../test/testUtils";
import type { MinimalResponse } from "../../test/testUtils";
import ProjectField from "./ProjectField";

/**
 * The project picker's own rows: the required mark reaches the control,
 * same-named projects stay apart by a short id suffix, and a full
 * read window says more may exist.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

function projectsRoute(rows: { project_id: string; name: string }[]): { method: string; path: string; reply: ReturnType<typeof respond> } {
  return { method: "GET", path: "/api/projects", reply: respond(200, { status: "success", result: rows }) };
}

const UUID_A = "aaaaaaaa-1111-4111-8111-111111111111";
const UUID_B = "bbbbbbbb-2222-4222-8222-222222222222";

describe("ProjectField", () => {
  it("marks the picker required for assistive tech", async () => {
    stubFetch([projectsRoute([{ project_id: UUID_A, name: "Grid Interviews" }])]);
    render(<ProjectField value="" onChange={() => {}} error="" />, { wrapper: makeWrapper() });
    const picker = await screen.findByRole("combobox");
    expect(picker.getAttribute("aria-required")).toBe("true");
    // The read asked for the picker's own window, not the server default.
    expect(calls.some((c) => c.url.includes("limit=50"))).toBe(true);
  });

  it("gives same-named projects a short id suffix", async () => {
    stubFetch([
      projectsRoute([
        { project_id: UUID_A, name: "Field Notes" },
        { project_id: UUID_B, name: "Field Notes" },
        { project_id: "cccccccc-3333-4333-8333-333333333333", name: "Unique name" },
      ]),
    ]);
    render(<ProjectField value="" onChange={() => {}} error="" />, { wrapper: makeWrapper() });
    fireEvent.mouseDown(await screen.findByRole("combobox"));
    // The duplicated name carries its short id; the unique one stays bare.
    expect(
      await screen.findByText(`Field Notes (${UUID_A.slice(0, 8)})`),
    ).toBeTruthy();
    expect(screen.getByText(`Field Notes (${UUID_B.slice(0, 8)})`)).toBeTruthy();
    expect(screen.getByText("Unique name")).toBeTruthy();
  });

  it("says more may exist when the read window is full", async () => {
    const many = Array.from({ length: 50 }, (_, index) => ({
      project_id: `dddddddd-0000-4000-8000-${String(index).padStart(12, "0")}`,
      name: `Project ${String(index + 1).padStart(2, "0")}`,
    }));
    stubFetch([projectsRoute(many)]);
    render(<ProjectField value="" onChange={() => {}} error="" />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.askProjectsCapped)).toBeTruthy();
  });
  it("retries with the same capped window, not the server default", async () => {
    // First read fails (503), the user hits retry: the refetch must
    // carry limit=50 again, or the capped note would sit over a larger
    // list than the one it names.
    const impl = (url: string | URL): Promise<MinimalResponse> => {
      const target = String(url);
      calls.push({ method: "GET", url: target, body: "" });
      if (target.includes("/api/projects")) {
        if (!target.includes("limit=50")) {
          return Promise.reject(new Error(`retry lost the window: ${target}`));
        }
        return Promise.resolve(
          respond(200, { status: "success", result: [{ project_id: UUID_A, name: "Grid Interviews" }] }),
        );
      }
      return Promise.reject(new Error(`unexpected fetch: ${target}`));
    };
    let failing = true;
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string | URL) => {
        if (failing) {
          failing = false;
          return Promise.resolve(
            respond(503, { status: "unavailable", message: "The server is down." }),
          );
        }
        return impl(url);
      }) as unknown as typeof fetch,
    );
    render(<ProjectField value="" onChange={() => {}} error="" />, { wrapper: makeWrapper() });
    fireEvent.click(await screen.findByRole("button", { name: strings.retryLabel }));
    expect(await screen.findByRole("combobox")).toBeTruthy();
    // Only the retry lands in the log (the failed mount read predates
    // it), and it names the capped window.
    const retryCall = calls.find((c) => c.url.includes("/api/projects"));
    expect(retryCall?.url).toContain("limit=50");
  });
});
