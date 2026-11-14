// Shapes for the statements family reads (Phase-2 Wave 3): the
// Statements card's list and history views. The SPA reads only these
// fields; anything else stays in the JsonViewer. The typeguard keeps a
// drifted payload on the honest fallback path (OpBody drift view)
// instead of crashing the card. Source: statements/store.py.

/** One statement atom (statements/store.py LIST_SQL / TIMELINE_SQL). */
export interface StatementRow {
  statement_id: string;
  text: string;
  template_id: string;
  valid_at: string | null;
  observed_at: string | null;
  confidence: number | null;
  superseded_by: string | null;
}

interface StatementsEnvelope {
  status: string;
  statements: StatementRow[];
  count: number;
}

export function isStatementsEnvelope(value: unknown): value is StatementsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).statements;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.statement_id === "string" &&
        typeof r.text === "string" &&
        typeof r.template_id === "string" &&
        (r.valid_at === null || typeof r.valid_at === "string") &&
        (r.observed_at === null || typeof r.observed_at === "string") &&
        (r.superseded_by === null || typeof r.superseded_by === "string")
      );
    })
  );
}
