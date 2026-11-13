// Import-graph gate (Phase-2 Wave 6, zero-drift rule 10): the graph
// map libraries stay out of the initial route. Two checks:
//   1. SOURCE: @xyflow/react and @dagrejs/dagre are imported ONLY in
//      the lazy Map module set (pages/Map.tsx + pages/map/).
//   2. BUILT OUTPUT: the entry route set (the same closure walk the
//      bundle gate uses) holds no chunk carrying the xyflow runtime
//      marker — the map libraries physically never reach a first visit.
// Wired as `poe ui-import-graph` and a ci.yml / ui step.
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// The ONLY modules allowed to import the map libraries (the lazy Map
// chunk's closure). Everything else fails the source scan.
const ALLOWED_PREFIXES = ["pages/Map.tsx", "pages/map/"];
const LIB_MARKER = "react-flow__node";
const LIB_PACKAGES = ["@xyflow/react", "@dagrejs/dagre"];

const uiRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const srcDir = join(uiRoot, "src");
const distDir = join(uiRoot, "..", "src", "corpus_kb", "ui_dist");
const distAssets = join(distDir, "assets");

function* sourceFiles(dir) {
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, name.name);
    if (name.isDirectory()) {
      yield* sourceFiles(path);
    } else if (/\.(ts|tsx)$/.test(name.name)) {
      yield path;
    }
  }
}

let failed = false;

// -- 1. Source scan --------------------------------------------------------
const offenders = [];
let importsSeen = 0;
for (const path of sourceFiles(srcDir)) {
  const text = readFileSync(path, "utf-8");
  const rel = path.slice(srcDir.length + 1).replaceAll("\\", "/");
  const pattern = new RegExp(`(?:from|import)\\s+["'](${LIB_PACKAGES.join("|")})(?:/|["'])`);
  if (pattern.test(text)) {
    importsSeen += 1;
    if (!ALLOWED_PREFIXES.some((prefix) => rel.startsWith(prefix))) {
      offenders.push(rel);
    }
  }
}
if (importsSeen === 0) {
  console.error("check-import-graph: no source file imports the map libraries — the scan is lying. Fix the marker list.");
  failed = true;
}
if (offenders.length > 0) {
  console.error(
    "check-import-graph: the map libraries are imported outside the lazy Map module:",
  );
  for (const rel of offenders) {
    console.error(`  ${rel}`);
  }
  failed = true;
}

// -- 2. Built-output scan --------------------------------------------------
const assets = readdirSync(distAssets).filter((n) => n.endsWith(".js") || n.endsWith(".css"));
let entry = null;
try {
  const html = readFileSync(join(distDir, "index.html"), "utf-8");
  entry = /assets\/([\w.-]+\.js)/.exec(html)?.[1] ?? null;
} catch {
  console.error("check-import-graph: cannot read index.html next to the assets.");
  process.exit(1);
}
if (entry === null || !assets.includes(entry)) {
  console.error("check-import-graph: entry chunk from index.html not found. Run the build first.");
  process.exit(1);
}

function staticImports(name) {
  const src = readFileSync(join(distAssets, name), "utf-8");
  const found = new Set();
  const re = /(?:from|import)\s*"\.\/([\w.-]+\.js)"/g;
  for (const match of src.matchAll(re)) {
    if (assets.includes(match[1])) {
      found.add(match[1]);
    }
  }
  return found;
}

const routeSet = new Set([entry]);
const queue = [entry];
while (queue.length > 0) {
  for (const dep of staticImports(queue.shift())) {
    if (!routeSet.has(dep)) {
      routeSet.add(dep);
      queue.push(dep);
    }
  }
}

const marked = assets.filter((name) => {
  try {
    return readFileSync(join(distAssets, name), "utf-8").includes(LIB_MARKER);
  } catch {
    return false;
  }
});
if (marked.length === 0) {
  console.error(
    "check-import-graph: no built chunk carries the xyflow marker — the map library never built. " +
      "Run the build; if the marker string changed upstream, update LIB_MARKER before trusting this gate.",
  );
  failed = true;
}
const inRoute = marked.filter((name) => routeSet.has(name));
if (inRoute.length > 0) {
  console.error("check-import-graph: xyflow chunks leaked into the initial route:");
  for (const name of inRoute) {
    console.error(`  ${name}`);
  }
  failed = true;
}

if (failed) {
  process.exit(1);
}
console.log(
  `check-import-graph: the map libraries stay in ${marked.length} lazy chunk(s); ` +
    `the initial route (${routeSet.size} chunks) is clean.`,
);
