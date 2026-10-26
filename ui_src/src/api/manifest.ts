// Typed access to the op manifest (the full entries; the generated TS
// table stays the narrow path/method/longRunning view for the hooks).
// manifest.json is copied from src/corpus_kb/ui_manifest.json by
// scripts/copy-manifest.mjs before lint/test/build — a shape drift fails
// this compile-time assertion.
import raw from "./manifest.json";

export interface ManifestEntry {
  id: string;
  path: string;
  method: string;
  description: string;
  required: string[];
  optional: string[];
  paramsKnown: boolean;
  longRunning: boolean;
}

export const MANIFEST: readonly ManifestEntry[] = raw;

/** The full manifest entry for one op id, or undefined when unknown. */
export function getEntry(opId: string): ManifestEntry | undefined {
  return MANIFEST.find((entry) => entry.id === opId);
}
