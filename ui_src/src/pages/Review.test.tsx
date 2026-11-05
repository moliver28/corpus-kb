import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch, stubFetchImpl } from "../test/testUtils";
import type { MinimalResponse } from "../test/testUtils";
import assignmentsOk from "../test/fixtures/assignments-ok-200.json";
import documentDeleteOk from "../test/fixtures/document-delete-ok-200.json";
import documentTagsOk from "../test/fixtures/document-tags-ok-200.json";
import documentsOk from "../test/fixtures/documents-ok-200.json";
import ingestStatusOk from "../test/fixtures/ingest-status-ok-200.json";
import metadataOk from "../test/fixtures/metadata-ok-200.json";
import reembedOk from "../test/fixtures/reembed-ok-200.json";
import reviewExecuteOk from "../test/fixtures/review-execute-ok-200.json";
import topicsOk from "../test/fixtures/topics-ok-200.json";
import Review from "./Review";

/**
 * The Review page from canonical fixtures: the documents table with its
 * drawer reads and the gated delete, the degraded-chunks banner with its
 * re-embed summary, and the accept/override flow where the override
 * needs a note and the reviewer name is remembered.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  clearCalls();
});

function reviewRoutes(): { method: string; path: string; reply: MinimalResponse }[] {
  return [
    // The per-document reads name a longer path than the list read, so
    // they must sit before the list route in this table.
    {
      method: "GET",
      path: `/api/documents/${documentsOk.response.body.result[0].doc_id}/tags`,
      reply: respond(200, documentTagsOk.response.body),
    },
    { method: "GET", path: "/api/metadata", reply: respond(200, metadataOk.response.body) },
    {
      method: "DELETE",
      path: `/api/documents/${documentDeleteOk.response.body.document_id}`,
      reply: respond(200, documentDeleteOk.response.body),
    },
    { method: "GET", path: "/api/documents", reply: respond(200, documentsOk.response.body) },
    { method: "GET", path: "/api/ingest/status", reply: respond(200, ingestStatusOk.response.body) },
    { method: "GET", path: "/api/coding/assignments", reply: respond(200, assignmentsOk.response.body) },
    { method: "GET", path: "/api/topics", reply: respond(200, topicsOk.response.body) },
  ];
}

describe("Review page", () => {
  it("renders the document rows with dates and chunk counts", async () => {
    stubFetch(reviewRoutes());
    render(<Review />, { wrapper: makeWrapper() });
    // The documents-list read answers on the same mount as the other
    // Review reads; the fixture window shows both rows.
    expect(await screen.findByText("corpus/renewable-energy-policy.md")).toBeTruthy();
    expect(screen.getByText("https://example.com/grid-reliability-report")).toBeTruthy();
    // The added date is the Intl word for the ISO stamp (fixed locale, UTC).
    expect(screen.getByText("Jan 05, 2026")).toBeTruthy();
    expect(screen.getByText("Jan 04, 2026")).toBeTruthy();
    expect(await screen.findByText(/lack usable embeddings/)).toBeTruthy();
  });

  it("opens the drawer with the document's tags and metadata", async () => {
    stubFetch(reviewRoutes());
    render(<Review />, { wrapper: makeWrapper() });
    const detailsButtons = await screen.findAllByRole("button", {
      name: strings.reviewDocsDetails,
    });
    fireEvent.click(detailsButtons[0]);
    expect(await screen.findByText("energy")).toBeTruthy();
    expect(screen.getByText("verified")).toBeTruthy();
    expect(await screen.findByText("A. Rowe")).toBeTruthy();
    expect(screen.getByText("en")).toBeTruthy();
    const docId = documentsOk.response.body.result[0].doc_id;
    expect(calls.some((c) => c.url.includes(`/api/documents/${docId}/tags`))).toBe(true);
    expect(calls.some((c) => c.url.includes(`/api/metadata?doc_id=${docId}`))).toBe(true);
  });

  it("deletes a document only through the named confirm gate", async () => {
    stubFetch(reviewRoutes());
    render(<Review />, { wrapper: makeWrapper() });
    const deleteButtons = await screen.findAllByRole("button", {
      name: strings.reviewDocsDelete,
    });
    fireEvent.click(deleteButtons[0]);
    // The gate names the document and the cascade before any call.
    expect(
      await screen.findByText(/renewable-energy-policy\.md.*chunks, vectors, tags/),
    ).toBeTruthy();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: strings.confirmConfirm }));
    expect(await screen.findByText(strings.reviewDocsDeleted)).toBeTruthy();
    const sent = calls.find((c) => c.method === "DELETE");
    expect(sent?.url).toContain(
      `/api/documents/${documentDeleteOk.response.body.document_id}`,
    );
    // The list refetches so the deleted row leaves the table.
    const listCalls = calls.filter((c) => c.url.includes("/api/documents?"));
    expect(listCalls.length).toBeGreaterThanOrEqual(2);
  });

  it("keeps cancel on the delete gate safe", async () => {
    stubFetch(reviewRoutes());
    render(<Review />, { wrapper: makeWrapper() });
    const deleteButtons = await screen.findAllByRole("button", {
      name: strings.reviewDocsDelete,
    });
    fireEvent.click(deleteButtons[0]);
    fireEvent.click(await screen.findByRole("button", { name: strings.confirmCancel }));
    expect(screen.queryByText(strings.reviewDocsDeleted)).toBeNull();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("never renders a slow tag answer for the document the user left", async () => {
    // The two tags reads are held and released by hand: the second
    // document's answer lands first, the first document's lands late.
    const held: ((reply: MinimalResponse) => void)[] = [];
    stubFetchImpl((url: string | URL, init?: { method?: string }) => {
      const method = (init?.method ?? "GET").toUpperCase();
      const u = String(url);
      const hit = reviewRoutes().find((r) => r.method === method && u.includes(r.path));
      if (hit !== undefined && u.includes("/tags")) {
        return new Promise<MinimalResponse>((resolve) => held.push(resolve));
      }
      calls.push({ method, url: u, body: "" });
      if (hit !== undefined) return Promise.resolve(hit.reply);
      return Promise.reject(new Error(`unexpected fetch: ${method} ${u}`));
    });
    render(<Review />, { wrapper: makeWrapper() });
    const detailsButtons = await screen.findAllByRole("button", {
      name: strings.reviewDocsDetails,
    });
    fireEvent.click(detailsButtons[0]);
    await waitFor(() => expect(held.length).toBe(1));
    fireEvent.click(detailsButtons[1]);
    await waitFor(() => expect(held.length).toBe(2));
    // The second document's answer lands first and renders.
    held[1](
      respond(200, {
        status: "success",
        result: [{ name: "fresh-b", color: null, description: null }],
      }),
    );
    expect(await screen.findByText("fresh-b")).toBeTruthy();
    // The first document's late answer may not touch the open drawer.
    held[0](
      respond(200, {
        status: "success",
        result: [{ name: "stale-a", color: null, description: null }],
      }),
    );
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.queryByText("stale-a")).toBeNull();
    expect(screen.getByText("fresh-b")).toBeTruthy();
  });

  it("falls back on a drifted documents payload", async () => {
    stubFetch([
      ...reviewRoutes().filter((r) => r.path !== "/api/documents"),
      {
        method: "GET",
        path: "/api/documents",
        reply: respond(200, { status: "success", result: [{ doc_id: 7, rows: "x" }] }),
      },
    ]);
    render(<Review />, { wrapper: makeWrapper() });
    expect(await screen.findByText(strings.unreadableResponse)).toBeTruthy();
    // The raw envelope stays one click away (the JsonViewer).
    expect(screen.getByText(strings.receiptRaw)).toBeTruthy();
  });

  it("pages through the server window", async () => {
    const many = Array.from({ length: 51 }, (_, i) => ({
      doc_id: `f0000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
      source: `corpus/doc-${String(i).padStart(3, "0")}.md`,
      source_type: "markdown",
      chunk_count: i + 1,
      created_at: "2026-01-05T10:12:00Z",
    }));
    stubFetch([
      ...reviewRoutes().filter((r) => r.path !== "/api/documents"),
      { method: "GET", path: "/api/documents", reply: respond(200, { status: "success", result: many }) },
    ]);
    render(<Review />, { wrapper: makeWrapper() });
    expect(await screen.findByText("corpus/doc-000.md")).toBeTruthy();
    // The first window asks for one row over the page size to learn
    // whether a next page exists.
    expect(
      calls.some((c) => c.url.includes("/api/documents?limit=51&offset=0")),
    ).toBe(true);
    const next = document.querySelector(".ant-pagination-next");
    expect(next).toBeTruthy();
    fireEvent.click(next as Element);
    // The window fetch for the next page rides the pager's state update.
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("offset=50"))).toBe(true),
    );
  });

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
    window.localStorage.setItem("corpus-kb:reviewer", "Alex");
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
    window.localStorage.setItem("corpus-kb:reviewer", "Alex");
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
    window.localStorage.setItem("corpus-kb:reviewer", "Alex");
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
