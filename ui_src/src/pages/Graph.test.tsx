import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import {
  calls,
  clearCalls,
  makeWrapper,
  respond,
  stubFetch,
  stubFetchImpl,
} from "../test/testUtils";
import Graph from "./Graph";

/**
 * The Graph stage from the manifest ops: the entities window table
 * (the shared server-window seam with the type filter), the create
 * forms through the form-to-write seam, and the entity picker that
 * keeps UUIDs out of the user's hands.
 */

const entitiesOk = {
  status: "success",
  result: [
    {
      entity_id: "00000000-0000-0000-0000-00000000ower",
      name: "Alpha",
      entity_type: "concept",
      metadata: { topic: "ingest" },
    },
    {
      entity_id: "00000000-0000-0000-0000-00000001wner",
      name: "Beta",
      entity_type: "idea",
      metadata: {},
    },
  ],
};

function entitiesRoute(rows: unknown[] = entitiesOk.result) {
  return { method: "GET", path: "/api/entities", reply: respond(200, { status: "success", result: rows }) };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

function entitiesCalls(): number {
  return calls.filter((c) => c.method === "GET" && c.url.includes("/api/entities")).length;
}

describe("Graph stage", () => {
  it("renders the entities window table through the seam", async () => {
    stubFetch([entitiesRoute()]);
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    expect(screen.getByText("Beta")).toBeTruthy();
  });

  it("applies the type filter to the window reads", async () => {
    stubFetch([entitiesRoute()]);
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    const before = entitiesCalls();
    const filter = screen.getByLabelText(strings.graphFilterLabel);
    await act(async () => {
      fireEvent.change(filter, { target: { value: "concept" } });
      fireEvent.blur(filter);
    });
    await waitFor(() => expect(entitiesCalls()).toBe(before + 1));
    const filtered = calls[calls.length - 1];
    expect(filtered.url).toContain("entity_type=concept");
    expect(filtered.url).toContain("offset=0");
  });

  it("rejects an empty entity name without a write", async () => {
    stubFetch([entitiesRoute()]);
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: strings.graphCreateTitle }));
    await waitFor(() =>
      expect(screen.getByText(strings.graphNameRequired)).toBeTruthy(),
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("creates an entity through the manifest op and refreshes the window", async () => {
    stubFetch([
      entitiesRoute(),
      { method: "POST", path: "/api/entities", reply: respond(200, {
        status: "success",
        entity_id: "00000000-0000-0000-0000-00000000new0",
        name: "Gamma",
        entity_type: "concept",
      }) },
    ]);
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    const before = entitiesCalls();
    fireEvent.change(screen.getByLabelText(`${strings.graphEntityNameLabel} *`), {
      target: { value: "Gamma" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.graphCreateTitle }));
    await waitFor(() => expect(screen.getByText(strings.graphCreateSaved)).toBeTruthy());
    const write = calls.find((c) => c.method === "POST" && c.url.includes("/api/entities"));
    expect(write).toBeDefined();
    expect(write?.body).toContain('"name":"Gamma"');
    expect(entitiesCalls()).toBeGreaterThan(before);
  });

  it("re-reads the pickers after an entity create and offers the new entity", async () => {
    // P1-1 (Wave-4 review): the write epoch lives on the page, so an
    // entity create re-reads BOTH pickers — the created entity is
    // pickable without a page reload. The stub serves a growing row
    // list, so the post-create picker read really carries Gamma.
    const rows = entitiesOk.result.map((row) => ({ ...row }));
    const seen: { method: string; url: string }[] = [];
    stubFetchImpl((url: string | URL, init?: { method?: string }) => {
      const method = (init?.method ?? "GET").toUpperCase();
      seen.push({ method, url: String(url) });
      if (method === "POST") {
        rows.push({
          entity_id: "00000000-0000-0000-0000-00000000new0",
          name: "Gamma",
          entity_type: "concept",
          metadata: {},
        });
        return Promise.resolve(
          respond(200, {
            status: "success",
            entity_id: "00000000-0000-0000-0000-00000000new0",
            name: "Gamma",
            entity_type: "concept",
          }),
        );
      }
      return Promise.resolve(respond(200, { status: "success", result: rows }));
    });
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    fireEvent.change(screen.getByLabelText(`${strings.graphEntityNameLabel} *`), {
      target: { value: "Gamma" },
    });
    fireEvent.click(screen.getByRole("button", { name: strings.graphCreateTitle }));
    await waitFor(() => expect(screen.getByText(strings.graphCreateSaved)).toBeTruthy());
    // The create must be followed by one fresh window read plus one
    // fresh read per picker (3 GET /api/entities calls; the picker and
    // the window both fetch one row past their cap since P3-6).
    const readsAfter = (log: { method: string; url: string }[]): number => {
      const postAt = log.findIndex((c) => c.method === "POST");
      expect(postAt).toBeGreaterThan(-1);
      return log.slice(postAt + 1).filter((c) => c.method === "GET" && c.url.includes("/api/entities"))
        .length;
    };
    await waitFor(() => expect(readsAfter(seen)).toBe(3));
    // The created entity is selectable in the source picker dropdown.
    fireEvent.mouseDown(screen.getAllByRole("combobox")[0]!);
    const openOptions = (): Element[] => {
      const dropdown = document.querySelector(
        ".ant-select-dropdown:not(.ant-select-dropdown-hidden):not(.ant-slide-up-leave)",
      );
      return dropdown ? Array.from(dropdown.querySelectorAll(".ant-select-item-option")) : [];
    };
    await waitFor(() => {
      const pick = openOptions().find((el) => el.textContent === "Gamma (concept)");
      if (pick === undefined) {
        throw new Error("created entity not in the open dropdown yet");
      }
    });
  });

  it("searches the server when the picker text narrows the list", async () => {
    // P2-3 (Wave-4 review): the picker feeds from search-graph on query,
    // so a corpus past the first window stays linkable. The search
    // answer replaces the window rows in the open dropdown.
    stubFetch([
      entitiesRoute(),
      { method: "POST", path: "/api/graph/search", reply: respond(200, {
        status: "success",
        result: [
          {
            entity_id: "00000000-0000-0000-0000-00000000new0",
            name: "Gamma",
            entity_type: "concept",
            metadata: {},
          },
        ],
      }) },
    ]);
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    const combo = screen.getAllByRole("combobox")[0]!;
    fireEvent.mouseDown(combo);
    fireEvent.change(combo, { target: { value: "Gam" } });
    await waitFor(() => {
      const posted = calls.find((c) => c.method === "POST" && c.url.includes("/api/graph/search"));
      if (posted === undefined) {
        throw new Error("the picker search has not fired yet");
      }
      expect(posted.body).toContain('"query":"Gam"');
    });
    const openOptions = (): Element[] => {
      const dropdown = document.querySelector(
        ".ant-select-dropdown:not(.ant-select-dropdown-hidden):not(.ant-slide-up-leave)",
      );
      return dropdown ? Array.from(dropdown.querySelectorAll(".ant-select-item-option")) : [];
    };
    await waitFor(() => {
      const pick = openOptions().find((el) => el.textContent === "Gamma (concept)");
      if (pick === undefined) {
        throw new Error("search answer not in the open dropdown yet");
      }
    });
  });

  it("keeps the picked label after the search text clears (Wave-4 rider)", async () => {
    // The Wave-4 re-review label edge: an entity picked from the search
    // answer sits outside the first window, so the cleared query (back
    // to the window rows) must not strip its visible label — the raw
    // UUID never becomes the control's label.
    stubFetch([
      entitiesRoute(),
      { method: "POST", path: "/api/graph/search", reply: respond(200, {
        status: "success",
        result: [
          {
            entity_id: "00000000-0000-0000-0000-00000000new0",
            name: "Gamma",
            entity_type: "concept",
            metadata: {},
          },
        ],
      }) },
    ]);
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    const combo = screen.getAllByRole("combobox")[0]!;
    fireEvent.mouseDown(combo);
    fireEvent.change(combo, { target: { value: "Gam" } });
    const openOptions = (): Element[] => {
      const dropdown = document.querySelector(
        ".ant-select-dropdown:not(.ant-select-dropdown-hidden):not(.ant-slide-up-leave)",
      );
      return dropdown ? Array.from(dropdown.querySelectorAll(".ant-select-item-option")) : [];
    };
    await waitFor(() => {
      const pick = openOptions().find((el) => el.textContent === "Gamma (concept)");
      if (pick === undefined) {
        throw new Error("search answer not in the open dropdown yet");
      }
    });
    await act(async () => {
      fireEvent.click(openOptions().find((el) => el.textContent === "Gamma (concept)")!);
    });
    // The query clears on pick (back to the window rows); the kept
    // label still names the picked row (antd 6 renders it in
    // .ant-select-content).
    const selection = document.querySelector(".ant-select-content");
    expect(selection?.textContent).toBe("Gamma (concept)");
  });

  it("reads the drawer's relations and neighbors for one entity", async () => {
    stubFetch([
      entitiesRoute(),
      { method: "GET", path: "/api/graph/relations/00000000-0000-0000-0000-00000000ower", reply: respond(200, {
        status: "success",
        result: [
          {
            relation_id: "00000000-0000-0000-0000-0000000000r1",
            relation_type: "related_to",
            weight: 1,
            source_entity_id: "00000000-0000-0000-0000-00000000ower",
            target_entity_id: "00000000-0000-0000-0000-00000001wner",
            source_name: "Alpha",
            target_name: "Beta",
          },
        ],
      }) },
      { method: "POST", path: "/api/graph/neighbors", reply: respond(200, {
        status: "success",
        result: [
          {
            entity_id: "00000000-0000-0000-0000-00000001wner",
            name: "Beta",
            entity_type: "idea",
            metadata: {},
            depth: 1,
          },
        ],
      }) },
    ]);
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("button", { name: strings.graphDetails })[0]!);
    const drawer = await screen.findByRole("dialog");
    // The relations read answers by path slot; the neighbors read is a
    // POST whose body names the entity (the row-read hook's body leg).
    await waitFor(() =>
      expect(within(drawer).getByText("Alpha related_to Beta")).toBeTruthy(),
    );
    expect(within(drawer).getAllByText("Beta").length).toBeGreaterThan(0);
    const neighborsRead = calls.find((c) => c.method === "POST" && c.url.includes("/api/graph/neighbors"));
    expect(neighborsRead?.body).toContain('"entity_id":"00000000-0000-0000-0000-00000000ower"');
    const relationsRead = calls.find((c) => c.url.includes("/api/graph/relations/"));
    expect(relationsRead?.url).toContain("00000000-0000-0000-0000-00000000ower");
  });

  it("keeps the relation form blocked until both endpoints are picked", async () => {
    stubFetch([entitiesRoute()]);
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: strings.graphRelationTitle }));
    await waitFor(() =>
      expect(screen.getByText(strings.graphSourceRequired)).toBeTruthy(),
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("writes the relation from picked endpoints, never typed ids", async () => {
    stubFetch([
      entitiesRoute(),
      { method: "POST", path: "/api/relations", reply: respond(200, {
        status: "success",
        relation_id: "00000000-0000-0000-0000-0000000000r1",
        relation_type: "related_to",
      }) },
    ]);
    render(<Graph />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getByText("Alpha")).toBeTruthy());
    // The pick targets the options of the one OPEN dropdown — a
    // leaving dropdown keeps its DOM until its leave motion ends
    // (ant-slide-up-leave), so it is excluded; the stage mounts two
    // pickers, and the closed one's dropdown must never match.
    const pickFrom = (): Element[] => {
      const dropdown = document.querySelector(
        ".ant-select-dropdown:not(.ant-select-dropdown-hidden):not(.ant-slide-up-leave)",
      );
      return dropdown
        ? Array.from(dropdown.querySelectorAll(".ant-select-item-option"))
        : [];
    };
    const pickEntity = async (index: number, label: string): Promise<void> => {
      // Re-query the combo: a picked source re-renders the pickers.
      fireEvent.mouseDown(screen.getAllByRole("combobox")[index]!);
      await waitFor(() => {
        const pick = pickFrom().find((el) => el.textContent === label);
        if (pick === undefined) {
          throw new Error(`option ${label} not in the open dropdown yet`);
        }
      });
      const pick = pickFrom().find((el) => el.textContent === label)!;
      await act(async () => {
        fireEvent.click(pick);
      });
    };
    // The stage mounts two pickers: source, then target.
    await pickEntity(0, "Alpha (concept)");
    await pickEntity(1, "Beta (idea)");
    fireEvent.click(screen.getByRole("button", { name: strings.graphRelationTitle }));
    await waitFor(() => expect(screen.getByText(strings.graphRelationSaved)).toBeTruthy());
    const write = calls.find((c) => c.method === "POST" && c.url.includes("/api/relations"));
    expect(write?.body).toContain('"source_entity_id":"00000000-0000-0000-0000-00000000ower"');
    expect(write?.body).toContain('"target_entity_id":"00000000-0000-0000-0000-00000001wner"');
  });
});
