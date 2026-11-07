// Shapes for the Graph stage reads. The SPA reads only these fields;
// anything else stays in the JsonViewer. The typeguards keep a drifted
// or unknown payload on the honest fallback path (DoctorCard pattern)
// instead of crashing the screen.

/** One graph entity row (the list-entities op). */
export interface EntityRow {
  entity_id: string;
  name: string;
  entity_type: string;
  metadata: Record<string, unknown>;
}

export interface EntitiesEnvelope {
  status: string;
  result: EntityRow[];
}

export function isEntitiesEnvelope(value: unknown): value is EntitiesEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).result;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.entity_id === "string" &&
        typeof r.name === "string" &&
        typeof r.entity_type === "string" &&
        typeof r.metadata === "object" &&
        r.metadata !== null &&
        !Array.isArray(r.metadata)
      );
    })
  );
}

/** One relation row (the get-entity-relations op; both endpoint names). */
export interface RelationRow {
  relation_id: string;
  relation_type: string;
  weight: number | null;
  source_name: string;
  target_name: string;
}

export interface RelationsEnvelope {
  status: string;
  result: RelationRow[];
}

export function isRelationsEnvelope(value: unknown): value is RelationsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).result;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.relation_id === "string" &&
        typeof r.relation_type === "string" &&
        typeof r.source_name === "string" &&
        typeof r.target_name === "string"
      );
    })
  );
}

/** One neighbor row (the graph-neighbors op; rows carry their depth). */
export interface NeighborRow {
  entity_id: string;
  name: string;
  entity_type: string;
  depth: number;
}

export interface NeighborsEnvelope {
  status: string;
  result: NeighborRow[];
}

export function isNeighborsEnvelope(value: unknown): value is NeighborsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).result;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.entity_id === "string" &&
        typeof r.name === "string" &&
        typeof r.entity_type === "string" &&
        typeof r.depth === "number"
      );
    })
  );
}
