import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearCalls, makeWrapper, respond, stubFetch } from "../../test/testUtils";
import Map from "../Map";

/**
 * The read-only prop pin (Wave 6 fix wave): the exact props the Map
 * mounts on the xyflow canvas, captured through a spy in ReactFlow's
 * place. This file sits inside the lazy Map module set, which is also
 * why the import-graph gate allows its `@xyflow/react` import. The
 * behavioral arm — select a node, press the delete keys, watch
 * nothing move — lives in pages/Map.test.tsx against the real xyflow.
 */

const captured: Array<Record<string, unknown>> = [];

vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...actual,
    ReactFlow: (props: Record<string, unknown>) => {
      captured.push(props);
      return null;
    },
  };
});

const oneEntity = {
  status: "success",
  result: [
    {
      entity_id: "00000000-0000-0000-0000-00000000ower",
      name: "Alpha",
      entity_type: "concept",
      metadata: {},
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
  captured.length = 0;
});

describe("Map read-only prop pin", () => {
  it("mounts the canvas with mutation muted and selection on", async () => {
    stubFetch([
      { method: "GET", path: "/api/entities", reply: respond(200, oneEntity) },
      { method: "GET", path: "/api/graph/relations", reply: respond(200, { status: "success", result: [] }) },
    ]);
    render(<Map />, { wrapper: makeWrapper() });
    await waitFor(() => expect(captured.length).toBeGreaterThan(0));
    const props = captured[captured.length - 1];
    expect(props.nodesDraggable).toBe(false);
    expect(props.nodesConnectable).toBe(false);
    // Selection is a view affordance and stays on; the delete key is
    // muted (xyflow's Backspace default would delete a drawn node).
    expect(props.elementsSelectable).toBe(true);
    expect(props.deleteKeyCode).toBeNull();
  });
});
