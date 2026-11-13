// The Map's pure data mapping (Phase-2 Wave 6): entities become
// positioned nodes (dagre, left to right), relations become edges.
// Pure functions so the window ordering stays deterministic and the
// unit tests can pin the mapping without a canvas.
//
// dagre and @xyflow/react are imported ONLY here and in Map.tsx — the
// import-graph gate (poe ui-import-graph) keeps them out of the entry
// chunk.
import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";
import type { EntityRow } from "../graph/shapes";
import type { RelationListRow } from "./shapes";

/** The entity node's data: the canvas draws the name and the type. */
export interface EntityNodeData {
  name: string;
  entityType: string;
  [key: string]: unknown;
}

export type EntityNode = Node<EntityNodeData, "entity">;

export interface RelationEdgeData {
  relationType: string;
  [key: string]: unknown;
}

export type RelationEdge = Edge<RelationEdgeData>;

const NODE_WIDTH = 168;
const NODE_HEIGHT = 48;
const NODES_SEP = 28;
const RANK_SEP = 120;

/**
 * Lay one server window of entities out left to right. The rows arrive
 * in the server's name order, so dagre starts from one deterministic
 * order and answers one deterministic layout (program rule 9).
 */
export function layoutEntities(
  rows: EntityRow[],
  relations: RelationListRow[],
): EntityNode[] {
  if (rows.length === 0) {
    return [];
  }
  const visible = new Set(rows.map((row) => row.entity_id));
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: "LR", nodesep: NODES_SEP, ranksep: RANK_SEP });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const row of rows) {
    graph.setNode(row.entity_id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  // Relations among visible nodes guide the layering; the rest are
  // layout-irrelevant (they draw from the edge mapper, not dagre).
  for (const relation of relations) {
    if (visible.has(relation.source_entity_id) && visible.has(relation.target_entity_id)) {
      graph.setEdge(relation.source_entity_id, relation.target_entity_id);
    }
  }
  dagre.layout(graph);
  const nodes: EntityNode[] = [];
  for (const row of rows) {
    const placed = graph.node(row.entity_id);
    if (placed === undefined) {
      continue;
    }
    nodes.push({
      id: row.entity_id,
      type: "entity",
      position: {
        x: (placed.x ?? 0) - NODE_WIDTH / 2,
        y: (placed.y ?? 0) - NODE_HEIGHT / 2,
      },
      data: { name: row.name, entityType: row.entity_type },
    });
  }
  return nodes;
}

/**
 * Map one window of relations to edges. Relations whose endpoints fall
 * outside the entity window are NOT drawn — the honest cap counts them
 * (the caller renders the count; nothing truncates silently).
 */
export function relationEdges(
  rows: RelationListRow[],
  visible: Set<string>,
): { edges: RelationEdge[]; outside: number } {
  const edges: RelationEdge[] = [];
  let outside = 0;
  for (const row of rows) {
    const inside =
      visible.has(row.source_entity_id) && visible.has(row.target_entity_id);
    if (!inside) {
      outside += 1;
      continue;
    }
    edges.push({
      id: row.relation_id,
      source: row.source_entity_id,
      target: row.target_entity_id,
      label: row.relation_type,
      data: { relationType: row.relation_type },
    });
  }
  return { edges, outside };
}
