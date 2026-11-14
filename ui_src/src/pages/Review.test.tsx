import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { MinimalResponse } from "../test/testUtils";
import assignmentsOk from "../test/fixtures/assignments-ok-200.json";
import documentDeleteOk from "../test/fixtures/document-delete-ok-200.json";
import documentStatsOk from "../test/fixtures/document-stats-ok-200.json";
import documentsOk from "../test/fixtures/documents-ok-200.json";
import ingestStatusOk from "../test/fixtures/ingest-status-ok-200.json";
import reembedOk from "../test/fixtures/reembed-ok-200.json";
import reviewExecuteOk from "../test/fixtures/review-execute-ok-200.json";
import statsOk from "../test/fixtures/stats-ok-200.json";
import topicsOk from "../test/fixtures/topics-ok-200.json";
import Review from "./Review";

/**
 * The Review page's coding-review half: the degraded-chunks banner with
 * its re-embed summary, the accept/override flow (the override needs a
 * note, the reviewer name is remembered), and the plain state words.
 * The documents card has its own file next to the component.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  clearCalls();
});

function reviewRoutes(): { method: string; path: string; reply: MinimalResponse }[] {
  return [
    // The documents card mounts with the page, so its list read answers
    // here too (its own rows live in review/DocumentsCard.test.tsx).
    {
      method: "DELETE",
      path: `/api/documents/${documentDeleteOk.response.body.document_id}`,
      reply: respond(200, documentDeleteOk.response.body),
    },
    { method: "GET", path: "/api/documents", reply: respond(200, documentsOk.response.body) },
    { method: "GET", path: "/api/ingest/status", reply: respond(200, ingestStatusOk.response.body) },
    { method: "GET", path: "/api/coding/assignments", reply: respond(200, assignmentsOk.response.body) },
    { method: "GET", path: "/api/topics", reply: respond(200, topicsOk.response.body) },
    // The Wave 3 stats strip rides the health area and reads on mount.
    { method: "GET", path: "/api/stats", reply: respond(200, statsOk.response.body) },
    { method: "GET", path: "/api/document-stats", reply: respond(200, documentStatsOk.response.body) },
  ];
}

describe("Review page", () => {
  it("renders the degraded counts and the re-embed summary", async () => {
    stubFetch([
      ...reviewRoutes(),
      { method: "POST", path: "/api/ingest/reembed", reply: respond(200, reembedOk.response.body) },
    ]);
    render(<Review />, { wrapper: makeWrapper() });
    expect(
      await screen.findByText(
        strings.reviewDegradedCounts.replace("{chunks}", "3").replace("{vectors}", "3"),
      ),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: strings.reviewDegradedAction }));
    // The bulk write never starts bare: the gate repeats the counts.
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(
        strings.reviewDegradedConfirmBody.replace("{chunks}", "3").replace("{vectors}", "3"),
      ),
    ).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: strings.confirmConfirm }));
    expect(
      await screen.findByText(
        strings.reviewDegradedDone
          .replace("{scanned}", "3")
          .replace("{fixed}", "3")
          .replace("{failed}", "0"),
      ),
    ).toBeTruthy();
  });

  it("disables the row actions while a decision is in flight", async () => {
    window.localStorage.setItem("corpus-kb:reviewer:v1", "Alex");
    let release!: (reply: MinimalResponse) => void;
    const gate = new Promise<MinimalResponse>((resolve) => {
      release = resolve;
    });
    const impl = (url: string | URL, init?: { method?: string }): Promise<MinimalResponse> => {
      const target = String(url);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ method, url: target, body: "" });
      if (method === "POST" && target.includes("/api/review/execute")) {
        return gate;
      }
      const hit = reviewRoutes().find((r) => r.method === method && target.includes(r.path));
      if (hit === undefined) {
        return Promise.reject(new Error(`unexpected fetch: ${method} ${target}`));
      }
      return Promise.resolve(hit.reply);
    };
    vi.stubGlobal("fetch", impl as unknown as typeof fetch);
    render(<Review />, { wrapper: makeWrapper() });
    const acceptButtons = await screen.findAllByRole("button", { name: strings.reviewAccept });
    expect((acceptButtons[0] as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(acceptButtons[0]);
    // In flight: both actions on every row sleep, so a double-click
    // cannot record the decision twice.
    await waitFor(() => {
      expect(
        (screen.getAllByRole("button", { name: strings.reviewAccept })[0] as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    });
    expect(
      (screen.getAllByRole("button", { name: strings.reviewOverride })[0] as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    release(respond(200, reviewExecuteOk.response.body));
    await screen.findByText(strings.reviewDecisionDone);
    const settled = await screen.findAllByRole("button", { name: strings.reviewAccept });
    expect((settled[0] as HTMLButtonElement).disabled).toBe(false);
  });

  it("records an accept decision with the remembered reviewer", async () => {
    window.localStorage.setItem("corpus-kb:reviewer:v1", "Alex");
    stubFetch([
      ...reviewRoutes(),
      {
        method: "POST",
        path: "/api/review/execute",
        reply: respond(200, reviewExecuteOk.response.body),
      },
    ]);
    render(<Review />, { wrapper: makeWrapper() });
    // Two fixture rows; the first row's Accept runs the decision flow.
    const acceptButtons = await screen.findAllByRole("button", { name: strings.reviewAccept });
    fireEvent.click(acceptButtons[0]);
    expect(await screen.findByText(strings.reviewDecisionDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/review/execute"));
    expect(sent).toBeTruthy();
    expect(sent?.body).toContain('"decision":"accept"');
    expect(sent?.body).toContain('"reviewer":"Alex"');
    // The server reviews the aggregate UUID, never the int row id.
    expect(sent?.body).toContain(
      '"assignment_id":"3f6d2c9a-1111-4c4c-8d8d-222222222222"',
    );
  });

  it("blocks an override until the note is written", async () => {
    window.localStorage.setItem("corpus-kb:reviewer:v1", "Alex");
    stubFetch([
      ...reviewRoutes(),
      {
        method: "POST",
        path: "/api/review/execute",
        reply: respond(200, reviewExecuteOk.response.body),
      },
    ]);
    render(<Review />, { wrapper: makeWrapper() });
    const overrideButtons = await screen.findAllByRole("button", { name: strings.reviewOverride });
    fireEvent.click(overrideButtons[0]);
    // The inline override form opens under the queue; the empty note
    // keeps the gate shut with the honest warning.
    fireEvent.click(
      await screen.findByRole("button", { name: strings.reviewOverrideSubmit }),
    );
    expect(await screen.findByText(strings.reviewNoteRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/review/execute"))).toBe(false);
    fireEvent.change(screen.getByPlaceholderText(strings.reviewNoteLabel), {
      target: { value: "Wrong code; the unit is about pricing." },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.reviewOverrideSubmit }));
    expect(await screen.findByText(strings.reviewDecisionDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/review/execute"));
    expect(sent?.body).toContain('"decision":"override"');
    expect(sent?.body).toContain("pricing");
    expect(sent?.body).toContain(
      '"assignment_id":"3f6d2c9a-1111-4c4c-8d8d-222222222222"',
    );
  });

  it("asks for a reviewer name before any decision", async () => {
    stubFetch(reviewRoutes());
    render(<Review />, { wrapper: makeWrapper() });
    const acceptButtons = await screen.findAllByRole("button", { name: strings.reviewAccept });
    fireEvent.click(acceptButtons[0]);
    expect(await screen.findByText(strings.reviewReviewerRequired)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/review/execute"))).toBe(false);
  });

  it("renders the queue rows with plain state words and the topics table", async () => {
    stubFetch(reviewRoutes());
    render(<Review />, { wrapper: makeWrapper() });
    expect(await screen.findByText("energy_policy")).toBeTruthy();
    expect(screen.getAllByText(strings.reviewStateNeedsReview).length).toBeGreaterThan(0);
    expect(screen.getAllByText(strings.reviewStateAuto).length).toBeGreaterThan(0);
    expect(screen.queryByText("review")).toBeNull();
    expect(await screen.findByText("Renewable energy policy")).toBeTruthy();
    expect(screen.getByText("62%")).toBeTruthy();
  });
});
