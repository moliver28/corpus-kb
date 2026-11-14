// Typed access to the op manifest (the bundle copy; the generated TS
// table stays the narrow path/method/longRunning view for the hooks).
// manifest.json is the generator's bundled copy of
// src/corpus_kb/ui_manifest.json WITHOUT the description keys — the SPA
// never renders them, so the bundle does not pay for that text (the
// Wave-5 headroom lever). tests/test_ui_manifest.py byte-compares the
// committed copy with the generated source, so a stale copy fails the
// Python gate. This compile-time assertion pins the entry SHAPE only.
import raw from "./manifest.json";

/** The verbs the client speaks — the generated op table's method union. */
type Method = "GET" | "POST" | "DELETE";

interface ManifestEntry {
  id: string;
  path: string;
  method: Method;
  required: string[];
  optional: string[];
  paramsKnown: boolean;
  longRunning: boolean;
}

// One boundary cast over the JSON import: the generator emits only the
// three verbs above (tests/test_ui_manifest.py pins every manifest method
// against the real route table), and JSON inference cannot say so.
const MANIFEST = raw as readonly ManifestEntry[];

/** The full manifest entry for one op id, or undefined when unknown. */
export function getEntry(opId: string): ManifestEntry | undefined {
  return MANIFEST.find((entry) => entry.id === opId);
}
