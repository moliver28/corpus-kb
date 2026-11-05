// Shapes for the labeling family reads and runs (Analyze screen). The
// SPA reads only these fields; anything else stays in the JsonViewer.
// The typeguards keep a drifted payload on the honest fallback path
// (DoctorCard pattern) instead of crashing the screen. Sources:
// tools/labeling_tools.py, tools/labeling_triage_tools.py,
// tools/labeling_gold_tools.py, labeling/writer.py.

import { percentText } from "../../api/format";

/** One built chunk label (labeling/writer.py read_labels; iso_or_none may answer null). */
export interface LabelRow {
  chunk_id: string;
  label: string;
  probability: number;
  created_at: string | null;
}

interface LabelsEnvelope {
  status: string;
  label_count: number;
  labels: LabelRow[];
}

export function isLabelsEnvelope(value: unknown): value is LabelsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const labels = (value as Record<string, unknown>).labels;
  return (
    Array.isArray(labels) &&
    labels.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.chunk_id === "string" &&
        typeof r.label === "string" &&
        typeof r.probability === "number" &&
        Number.isFinite(r.probability) &&
        (r.created_at === null || typeof r.created_at === "string")
      );
    })
  );
}

/** One registered labeling function with its scorecard (live_stats). */
export interface LfRow {
  name: string;
  kind: string;
  label: string | null;
  stats: {
    coverage: number;
    conflicts: number;
    sample_chunks: number;
  } | null;
}

interface LfsEnvelope {
  status: string;
  lf_count: number;
  lfs: LfRow[];
}

export function isLfsEnvelope(value: unknown): value is LfsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const lfs = (value as Record<string, unknown>).lfs;
  return (
    Array.isArray(lfs) &&
    lfs.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      const labelOk = r.label === null || typeof r.label === "string";
      if (r.stats === null || r.stats === undefined) {
        return typeof r.name === "string" && typeof r.kind === "string" && labelOk;
      }
      if (typeof r.stats !== "object" || r.stats === null) return false;
      const s = r.stats as Record<string, unknown>;
      return (
        typeof r.name === "string" &&
        typeof r.kind === "string" &&
        labelOk &&
        typeof s.coverage === "number" &&
        Number.isFinite(s.coverage) &&
        typeof s.conflicts === "number" &&
        Number.isFinite(s.conflicts)
      );
    })
  );
}

/** One ranked review row (labeling_triage_ops.py queue_row). */
export interface TriageRow {
  chunk_id: string;
  assigned_label: string;
  oos_probability: number;
  runner_up_label: string;
  runner_up_probability: number;
}

interface TriageEnvelope {
  status: string;
  n_flagged: number;
  queue: TriageRow[];
}

export function isTriageEnvelope(value: unknown): value is TriageEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  if (typeof v.n_flagged !== "number" || !Array.isArray(v.queue)) return false;
  return v.queue.every((row) => {
    if (typeof row !== "object" || row === null) return false;
    const r = row as Record<string, unknown>;
    return (
      typeof r.chunk_id === "string" &&
      typeof r.assigned_label === "string" &&
      typeof r.oos_probability === "number" &&
      Number.isFinite(r.oos_probability) &&
      typeof r.runner_up_label === "string"
    );
  });
}

/** One class's gold validation (labeling/ppi_check.py GoldValidation). */
export interface GoldRow {
  class_name: string;
  status: string;
  raw_accuracy: number;
  estimate: number;
  lower: number | null;
  upper: number | null;
  n_gold: number;
}

interface GoldEnvelope {
  status: string;
  class_count: number;
  validations: GoldRow[];
}

export function isGoldEnvelope(value: unknown): value is GoldEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const validations = (value as Record<string, unknown>).validations;
  return (
    Array.isArray(validations) &&
    validations.every((row) => {
      if (typeof row !== "object" || row === null) return false;
      const r = row as Record<string, unknown>;
      return (
        typeof r.class_name === "string" &&
        typeof r.status === "string" &&
        typeof r.raw_accuracy === "number" &&
        Number.isFinite(r.raw_accuracy) &&
        typeof r.estimate === "number" &&
        Number.isFinite(r.estimate) &&
        (r.lower === null || typeof r.lower === "number") &&
        (r.upper === null || typeof r.upper === "number")
      );
    })
  );
}

/**
 * The reason + remedy words of an honest non-success envelope
 * (insufficient_data and friends carry both on the same keys).
 */
export function envelopeNote(data: unknown): { reason: string; remedy: string } {
  if (typeof data !== "object" || data === null) {
    return { reason: "", remedy: "" };
  }
  const v = data as Record<string, unknown>;
  return {
    reason: typeof v.reason === "string" ? v.reason : "",
    remedy: typeof v.remedy === "string" ? v.remedy : "",
  };
}

/** One interval cell -> "12% - 48%"; a missing bound drops its side. */
export function rangeText(lower: number | null, upper: number | null): string {
  const low = percentText(lower);
  const high = percentText(upper);
  if (low === "" && high === "") return "";
  if (low === "") return `<= ${high}`;
  if (high === "") return `>= ${low}`;
  return `${low} - ${high}`;
}
