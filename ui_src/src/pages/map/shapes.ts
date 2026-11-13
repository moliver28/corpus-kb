// Shapes for the Map stage reads (Phase-2 Wave 6). Same contract as
// the Graph stage shapes: the SPA reads only these fields, and a
// drifted payload falls to the honest OpBody paths, never a crash.

/** One relation row (the list-relations op; both endpoint names ride it). */
export interface RelationListRow {
  relation_id: string;
  source_entity_id: string;
  target_entity_id: string;
  relation_type: string;
  weight: number;
  source_name: string;
  target_name: string;
}

export interface RelationsEnvelope {
  status: string;
  result: RelationListRow[];
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
        typeof r.source_entity_id === "string" &&
        typeof r.target_entity_id === "string" &&
        typeof r.relation_type === "string" &&
        typeof r.weight === "number" &&
        typeof r.source_name === "string" &&
        typeof r.target_name === "string"
      );
    })
  );
}
