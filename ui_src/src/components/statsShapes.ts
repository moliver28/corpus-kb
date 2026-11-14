// Shapes for the two corpus-stats reads (Phase-2 Wave 3), beside the
// one component that renders them (StatsStrip). The SPA reads only
// these fields; anything else stays in the JsonViewer. The typeguards
// keep a drifted payload on the honest fallback path (OpBody drift
// view) instead of crashing the strip. Source: tools/versioning_tools.py.

/** One corpus stats answer: one row count per key. */
interface StatsEnvelope {
  status: string;
  stats: Record<string, number>;
}

export function isStatsEnvelope(value: unknown): value is StatsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const stats = (value as Record<string, unknown>).stats;
  if (typeof stats !== "object" || stats === null) return false;
  return Object.values(stats).every((n) => typeof n === "number" && Number.isFinite(n));
}

/** One per-source-type count row (versioning_handler.py document stats). */
export interface SourceTypeRow {
  source_type: string;
  count: number;
}

interface DocumentStatsEnvelope {
  status: string;
  by_source_type: SourceTypeRow[];
}

export function isDocumentStatsEnvelope(value: unknown): value is DocumentStatsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).by_source_type;
  return (
    Array.isArray(rows) &&
    rows.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.source_type === "string" &&
        typeof r.count === "number" &&
        Number.isFinite(r.count)
      );
    })
  );
}
