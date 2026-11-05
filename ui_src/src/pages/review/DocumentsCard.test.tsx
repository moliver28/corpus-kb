import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch, stubFetchImpl } from "../../test/testUtils";
import type { MinimalResponse } from "../../test/testUtils";
import documentDeleteOk from "../../test/fixtures/document-delete-ok-200.json";
import documentTagsOk from "../../test/fixtures/document-tags-ok-200.json";
import documentsOk from "../../test/fixtures/documents-ok-200.json";
import metadataOk from "../../test/fixtures/metadata-ok-200.json";
import DocumentsCard from "./DocumentsCard";

/**
 * The documents card from canonical fixtures: the window table with its
 * Intl dates, the per-document drawer reads, the gated delete, and the
 * honest drift fallback. The row-read race (a slow answer never lands
 * under the wrong document) is pinned here too.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

function documentRoutes(): { method: string; path: string; reply: MinimalResponse }[] {
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
  ];
}

describe("DocumentsCard", () => {
  it("renders the document rows with dates and chunk counts", async () => {
    stubFetch(documentRoutes());
    render(<DocumentsCard />, { wrapper: makeWrapper() });
    expect(await screen.findByText("corpus/renewable-energy-policy.md")).toBeTruthy();
    expect(screen.getByText("https://example.com/grid-reliability-report")).toBeTruthy();
    // The added date is the Intl word for the ISO stamp (fixed locale, UTC).
    expect(screen.getByText("Jan 05, 2026")).toBeTruthy();
    expect(screen.getByText("Jan 04, 2026")).toBeTruthy();
  });

  it("opens the drawer with the document's tags and metadata", async () => {
    stubFetch(documentRoutes());
    render(<DocumentsCard />, { wrapper: makeWrapper() });
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
    stubFetch(documentRoutes());
    render(<DocumentsCard />, { wrapper: makeWrapper() });
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
    stubFetch(documentRoutes());
    render(<DocumentsCard />, { wrapper: makeWrapper() });
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
      const hit = documentRoutes().find((r) => r.method === method && u.includes(r.path));
      if (hit !== undefined && u.includes("/tags")) {
        return new Promise<MinimalResponse>((resolve) => held.push(resolve));
      }
      calls.push({ method, url: u, body: "" });
      if (hit !== undefined) return Promise.resolve(hit.reply);
      return Promise.reject(new Error(`unexpected fetch: ${method} ${u}`));
    });
    render(<DocumentsCard />, { wrapper: makeWrapper() });
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
      {
        method: "GET",
        path: "/api/documents",
        reply: respond(200, { status: "success", result: [{ doc_id: 7, rows: "x" }] }),
      },
    ]);
    render(<DocumentsCard />, { wrapper: makeWrapper() });
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
      {
        method: "GET",
        path: "/api/documents",
        reply: respond(200, { status: "success", result: many }),
      },
    ]);
    render(<DocumentsCard />, { wrapper: makeWrapper() });
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
});
