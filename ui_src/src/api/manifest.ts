// Typed access to the op manifest (the full entries; the generated TS
// table stays the narrow path/method/longRunning view for the hooks).
// manifest.json is a build-time copy of src/corpus_kb/ui_manifest.json
// (scripts/copy-manifest.mjs runs before lint/test/build). The pre-hooks
// do not guarantee the sync: tests/test_ui_manifest.py byte-compares the
// committed copy with the generated source. This compile-time assertion
// pins the entry SHAPE only — same-shape content drift is caught there.
import raw from "./manifest.json";

/** The verbs the client speaks — the generated op table's method union. */
type Method = "GET" | "POST" | "DELETE";

interface ManifestEntry {
  id: string;
  path: string;
  method: Method;
  description: string;
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
