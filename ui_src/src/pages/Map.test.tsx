import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import Map from "./Map";
import { layoutEntities, relationEdges } from "./map/layout";
import type { RelationListRow } from "./map/shapes";

/**
 * The Map stage (Phase-2 Wave 6): nodes and edges from the two window
 * reads, the honest caps and empty/error states, and the tables that
 * carry the same data as the canvas.
 */

const entitiesOk = {
  status: "success",
  result: [
    {
      entity_id: "00000000-0000-0000-0000-00000000ower",
      name: "Alpha",
      entity_type: "concept",
      metadata: {},
    },
    {
      entity_id: "00000000-0000-0000-0000-00000001wner",
      name: "Beta",
      entity_type: "idea",
      metadata: {},
    },
    {
      entity_id: "00000000-0000-0000-0000-00000002utside",
      name: "Gamma",
      entity_type: "concept",
      metadata: {},
    },
  ],
};

const relationsOk = {
  status: "success",
  result: [
    {
      relation_id: "00000000-0000-0000-0000-0000000000r1",
      source_entity_id: "00000000-0000-0000-0000-00000000ower",
      target_entity_id: "00000000-0000-0000-0000-00000001wner",
      relation_type: "related_to",
      weight: 1,
      source_name: "Alpha",
      target_name: "Beta",
    },
    {
      relation_id: "00000000-0000-0000-0000-0000000000r2",
      source_entity_id: "00000000-0000-0000-0000-00000002utside",
      target_entity_id: "00000000-0000-0000-0000-00000009faraway",
      relation_type: "references",
      weight: 1,
      source_name: "Gamma",
      target_name: "Delta",
    },
  ],
};

function entityRoute(rows: unknown[] = entitiesOk.result) {
  return { method: "GET", path: "/api/entities", reply: respond(200, { status: "success", result: rows }) };
}

function relationRoute(rows: unknown[] = relationsOk.result) {
  return { method: "GET", path: "/api/graph/relations", reply: respond(200, { status: "success", result: rows }) };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

function windowRows(count: number): unknown[] {
  return Array.from({ length: count }, (_, index) => ({
    entity_id: `00000000-0000-0000-0000-${String(index).padStart(12, "0")}`,
    name: `entity-${String(index).padStart(3, "0")}`,
    entity_type: "concept",
    metadata: {},
  }));
}

describe("Map data mapping", () => {
  it("lays entities out deterministically", () => {
    const rows = entitiesOk.result.map((row) => ({
      ...row,
      metadata: {} as Record<string, unknown>,
    }));
    const first = layoutEntities(rows, []);
    const second = layoutEntities(rows, []);
    expect(first).toHaveLength(3);
    expect(first.map((node) => node.id)).toEqual(second.map((node) => node.id));
    expect(first.map((node) => node.position)).toEqual(second.map((node) => node.position));
    expect(first[0]?.data.name).toBe("Alpha");
    expect(first[0]?.type).toBe("entity");
  });

  it("draws in-window relations and counts the outside ones", () => {
    const rows = relationsOk.result as RelationListRow[];
    const visible = new Set(["00000000-0000-0000-0000-00000000ower", "00000000-0000-0000-0000-00000001wner"]);
    const { edges, outside } = relationEdges(rows, visible);
    expect(edges).toHaveLength(1);
    expect(edges[0]?.id).toBe("00000000-0000-0000-0000-0000000000r1");
    expect(edges[0]?.source).toBe("00000000-0000-0000-0000-00000000ower");
    expect(edges[0]?.label).toBe("related_to");
    expect(outside).toBe(1);
  });
});

describe("Map stage", () => {
  it("renders the canvas, the node names, and the relations table from the two reads", async () => {
    stubFetch([entityRoute(), relationRoute()]);
    render(<Map />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getAllByText("Alpha").length).toBeGreaterThan(0));
    expect(document.querySelector(".react-flow")).toBeTruthy();
    // The in-window relation draws as an edge AND rides the honest
    // relations table (source, type, target cells); the outside
    // relation appears ONLY in the table (no silent truncation).
    expect(screen.getByText("related_to")).toBeTruthy();
    expect(screen.getByText("references")).toBeTruthy();
    expect(screen.getByText("Delta")).toBeTruthy();
  });

  it("keeps the canvas read-only (no handles, no drag)", async () => {
    stubFetch([entityRoute(), relationRoute()]);
    render(<Map />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getAllByText("Alpha").length).toBeGreaterThan(0));
    // nodesConnectable={false} hides every handle; nothing on the pane
    // starts a write op (the page never mounts a write hook).
    expect(document.querySelector(".react-flow__handle")).toBeNull();
  });

  it("names the honest entity cap when the server holds one more", async () => {
    stubFetch([
      entityRoute(windowRows(51)),
      relationRoute([]),
    ]);
    render(<Map />, { wrapper: makeWrapper() });
    await waitFor(() =>
      expect(screen.getByText(strings.mapCappedEntities.replace("{shown}", "50"))).toBeTruthy(),
    );
    expect(screen.queryByText("entity-050")).toBeNull();
  });

  it("counts the relations the window cannot draw", async () => {
    stubFetch([entityRoute(), relationRoute()]);
    render(<Map />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getAllByText("Alpha").length).toBeGreaterThan(0));
    expect(
      screen.getByText(strings.mapOutsideNote.replace("{count}", "1")),
    ).toBeTruthy();
  });

  it("answers the honest empty state when the graph is empty", async () => {
    stubFetch([entityRoute([]), relationRoute([])]);
    render(<Map />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getAllByText(strings.mapEmpty).length).toBeGreaterThan(0));
    expect(screen.getByText(strings.mapRelationsEmpty)).toBeTruthy();
    expect(document.querySelector(".react-flow")).toBeNull();
  });

  it("renders the honest warn block when a read drifts", async () => {
    stubFetch([
      { method: "GET", path: "/api/entities", reply: respond(500, { status: "error" }) },
      relationRoute(),
    ]);
    render(<Map />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getAllByText(strings.unreadableResponse).length).toBeGreaterThan(0));
    expect(document.querySelector(".react-flow")).toBeNull();
  });

  it("carries the same data in the entities table under the canvas", async () => {
    stubFetch([entityRoute(), relationRoute()]);
    render(<Map />, { wrapper: makeWrapper() });
    await waitFor(() => expect(screen.getAllByText("Alpha").length).toBeGreaterThan(0));
    // The fallback table carries name, type, and id columns with the
    // canvas's entity ids (the canvas never holds the only path).
    expect(screen.getAllByRole("columnheader", { name: strings.mapColumnName }).length).toBeGreaterThan(0);
    expect(screen.getAllByText("00000000-0000-0000-0000-00000000ower").length).toBeGreaterThan(0);
  });
});
